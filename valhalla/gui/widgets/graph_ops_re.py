"""
The routing-earth.com operations behind the unified graphs table: init /
adopt / sync / status, all as ``routing_earth_utils.cli`` subprocesses (see
core/routing_earth.py for why in-process import is impossible). Extracts live
inside the graph library (``<graph_dir>/<name>/``), like every other graph. No
table/chrome concerns in here — the widget injects callables.
"""

import json
import re
from pathlib import Path
from shutil import move
from typing import Callable, List, Optional

from qgis.core import Qgis
from qgis.PyQt.QtCore import QObject, QProcess
from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from ...core import graph_registry
from ...core.routing_earth import Entitlement, get_re_api_key, re_cli_args, re_process_env
from ...core.settings import get_settings_dir
from ...global_definitions import PYTHON_EXE


class RoutingEarthController(QObject):
    """Owns the one-at-a-time `re` subprocess and the RE op flows."""

    def __init__(
        self,
        parent_widget,
        log: Callable[[str], None],
        status_bar,
        refresh: Callable[[], None],
        confirm_replace: Callable[[str], bool],
    ):
        super().__init__(parent_widget)
        self._widget = parent_widget  # dialog parenting only
        self._log = log
        self._status_bar = status_bar
        self._refresh = refresh
        self._confirm_replace = confirm_replace

        self._proc_buf = ""
        self._on_proc_done: Optional[Callable[[int], None]] = None
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        # cwd must not contain a `valhalla` package (same shadowing as in-process)
        self.proc.setWorkingDirectory(str(get_settings_dir()))
        self.proc.readyReadStandardOutput.connect(self._on_log_ready)
        self.proc.finished.connect(self._on_proc_finished)

    # ---------------- subprocess plumbing ----------------

    def _run_re(self, args: List[str], on_done: Callable[[int], None]) -> bool:
        """Starts one CLI invocation; False if busy or the API key is missing."""
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self._status_bar.pushWarning(
                "Busy", "Another routing.earth operation is running, try again after it finished..."
            )
            return False
        api_key = get_re_api_key()
        if not api_key:
            self._status_bar.pushMessage(
                "No API key — paste your routing.earth API key above first",
                Qgis.MessageLevel.Critical,
                6,
            )
            return False

        self._proc_buf = ""
        self._on_proc_done = on_done
        self.proc.setProcessEnvironment(re_process_env(api_key))
        self.proc.start(str(PYTHON_EXE), args)
        self._log(f"Executing re {' '.join(args[2:])}")
        return True

    def _on_log_ready(self):
        log = self.proc.readAll().data().decode()
        self._proc_buf += log
        self._log(log.rstrip("\n"))

    def _on_proc_finished(self, exit_code: int, exit_status: QProcess.ExitStatus):
        on_done, self._on_proc_done = self._on_proc_done, None
        if exit_status == QProcess.ExitStatus.CrashExit:
            exit_code = -1
        if on_done:
            on_done(exit_code)

    def _osm_from_log(self) -> dict:
        """The resolver's OSM data timestamp, as logged/printed by the CLI."""
        found = re.findall(r"osm data (\S+)", self._proc_buf)
        ts = found[-1] if found else ""
        return {"osm_data_timestamp": ts} if ts and ts != "None" else {}

    def _diff_from_log(self) -> dict:
        """The size of what the CLI just downloaded, from its output:
        the `synced ... (<verdict>, N bundle(s), 5.0 MiB)` result line, or the
        seed download log on init; "already current" downloaded nothing."""
        if found := re.search(r"bundle\(s\), ([\d.]+ (?:B|KiB|MiB|GiB))\)", self._proc_buf):
            return {"last_diff": found.group(1)}
        if found := re.search(r"full snapshot \((\d+) bytes\)", self._proc_buf):
            return {"last_diff": graph_registry.human_size(int(found.group(1)))}
        if "already current" in self._proc_buf:
            return {"last_diff": "0 B"}
        return {}

    def _json_from_buf(self) -> dict:
        """The last JSON doc in the (merged stdout+stderr) buffer — `re status` output."""
        for line in reversed(self._proc_buf.splitlines()):
            try:
                doc = json.loads(line)
                if isinstance(doc, dict):
                    return doc
            except json.JSONDecodeError:
                continue
        return {}

    # ---------------- operations ----------------

    def init_package(self, ent: Entitlement):
        """Seeds a fresh extract for an entitled package. A concrete scope goes
        straight to a sized confirm; a wildcard first prompts for the region."""
        scope, cadence = ent.scope, ent.cadence
        if scope == "*":
            scope, ok = QInputDialog.getText(
                self._widget,
                "routing.earth region",
                f"Region id to download ({cadence}):",
            )
            scope = scope.strip()
            if not ok or not scope:
                return

        if not self._confirm_download(scope, cadence, ent):
            return

        name = f"{scope}_{cadence}"
        if graph_registry.entry_exists(name):
            if not self._confirm_replace(name):
                return
            graph_registry.unregister(name)  # `re init` needs a fresh tar path
        entry_dir = graph_registry.graph_dir().joinpath(name)
        entry_dir.mkdir(parents=True, exist_ok=True)
        tar_path = entry_dir.joinpath(f"{name}.tar")

        def done(exit_code: int):
            if exit_code != 0:
                self._status_bar.pushMessage(
                    "Initializing the package failed, see log!", Qgis.MessageLevel.Critical, 0
                )
                return
            self._register_managed(
                name,
                tar_path,
                scope,
                cadence,
                "initialized",
                **self._osm_from_log(),
                **self._diff_from_log(),
            )

        if self._run_re(re_cli_args("init", tar_path, "--scope", scope, "--cadence", cadence), done):
            self._status_bar.pushInfo(
                "", f"Started seeding {scope}/{cadence} — this can take a while..."
            )

    def _confirm_download(self, scope: str, cadence: str, ent: Entitlement) -> bool:
        dl = graph_registry.human_size(ent.compressed_size_bytes)
        disk = graph_registry.human_size(ent.size_bytes)
        if dl and disk:
            size_note = f" (~{dl} download, ~{disk} on disk)"
        elif dl:
            size_note = f" (~{dl} download)"
        else:
            size_note = ""
        return (
            QMessageBox.question(
                self._widget,
                "Download graph package",
                f"Download {scope}/{cadence}{size_note}?\nThis can take a while.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            == QMessageBox.StandardButton.Yes
        )

    def adopt(self):
        """Moves an already-managed extract into the library and registers it."""
        src, _ = QFileDialog.getOpenFileName(
            self._widget, "Adopt a managed extract", str(Path.home()), "Tar files (*.tar)"
        )
        if not src:
            return
        state = graph_registry.read_tar_state(Path(src))
        if state is None:
            self._status_bar.pushMessage(
                f"{Path(src).name} is not a routing.earth managed extract — "
                "only extracts seeded by 'init' can be registered and synced",
                Qgis.MessageLevel.Critical,
                0,
            )
            return
        scope, cadence = state["scope"], state["cadence"]
        name = f"{scope}_{cadence}"
        if graph_registry.entry_exists(name):
            if not self._confirm_replace(name):
                return
            graph_registry.unregister(name)
        entry_dir = graph_registry.graph_dir().joinpath(name)
        entry_dir.mkdir(parents=True, exist_ok=True)
        dest = entry_dir.joinpath(f"{name}.tar")
        move(str(src), str(dest))
        self._register_managed(name, dest, scope, cadence, "adopted")

    def _register_managed(
        self, name: str, tar_path: Path, scope: str, cadence: str, verb: str, **state_extra
    ):
        """Writes the id.json (with its ``routing_earth`` block) for a managed
        extract that already sits at ``tar_path`` inside the library."""
        entry_dir = graph_registry.register(
            name,
            graph_registry.re_graph_config(str(tar_path.resolve()), scope, cadence),
            replace=True,  # collisions were confirmed above (init: before the download)
        )
        graph_registry.mark_synced(entry_dir, **state_extra)
        self._refresh()
        self._status_bar.pushMessage(
            f"Successfully {verb} graph package {name}", Qgis.MessageLevel.Success, 3
        )

    def sync(self, entry: graph_registry.GraphEntry):
        def done(exit_code: int):
            if exit_code != 0:
                self._status_bar.pushMessage(
                    f"Syncing {entry.name} failed, see log!", Qgis.MessageLevel.Critical, 0
                )
                return
            graph_registry.mark_synced(
                entry.entry_dir,
                behind="",
                **self._osm_from_log(),
                **self._diff_from_log(),
            )
            self._refresh()
            self._status_bar.pushMessage(
                f"Successfully synced {entry.name}", Qgis.MessageLevel.Success, 3
            )

        if self._run_re(re_cli_args("sync", entry.tar_path), done):
            self._status_bar.pushInfo("", f"Started syncing {entry.name}...")

    def status_all(self, entries: List[graph_registry.GraphEntry]):
        """Checks every managed package against the resolver, one subprocess at a time."""
        queue = [e for e in entries if e.is_managed]
        if not queue:
            self._refresh()
            return

        def run_next():
            if not queue:
                self._refresh()
                self._status_bar.pushInfo("", "Finished checking package status")
                return
            entry = queue.pop(0)

            def done(exit_code: int):
                # `re status` is git-diff style: exit 0 = current, 2 = behind
                if exit_code in (0, 2):
                    doc = self._json_from_buf()
                    extra = {"behind": "", "latest_osm": ""}
                    if exit_code == 2:
                        extra["behind"] = str(doc.get("latest_dataset_id") or "")
                        # the reported osm timestamp describes the LATEST
                        # dataset, not this extract — tooltip only
                        extra["latest_osm"] = doc.get("osm_data_timestamp") or ""
                    elif doc.get("osm_data_timestamp"):
                        extra["osm_data_timestamp"] = doc["osm_data_timestamp"]
                    # status is not a sync: merges into the RE block, synced_at stays
                    graph_registry.set_re_state(entry.entry_dir, **extra)
                run_next()

            self._run_re(re_cli_args("status", entry.tar_path), done)

        run_next()
