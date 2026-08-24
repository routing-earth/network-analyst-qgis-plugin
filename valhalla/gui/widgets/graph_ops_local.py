"""
The local-graph operations behind the unified graphs table ("Advanced" menu):
add a tar in place, register a tile_url graph, and the chained
``valhalla_build_admins``/``valhalla_build_tiles`` PBF build. No table/chrome
concerns in here — the widget injects callables.
"""

import json
import os
from pathlib import Path
from shutil import move
from typing import Callable

from qgis.core import Qgis
from qgis.PyQt.QtCore import QDir, QObject, QProcess
from qgis.PyQt.QtWidgets import QDialog, QFileDialog

from ...core import graph_registry
from ...core.settings import ValhallaSettings
from ..dlg_graph_from_pbf import GraphFromPBFDialog
from ..dlg_graph_from_url import GraphFromURLDialog


class LocalGraphController(QObject):
    """Owns the build processes and the non-RE add flows."""

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

        self.from_pbf_dlg = GraphFromPBFDialog(parent_widget)
        self.from_pbf_dlg.finished.connect(self._on_graph_add_build)
        self.from_url_dlg = GraphFromURLDialog(parent_widget)
        self.from_url_dlg.finished.connect(self._on_graph_add_url)

        def make_build_process() -> QProcess:
            proc = QProcess(self)
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            proc.readyReadStandardOutput.connect(self._on_build_log_ready)
            return proc

        self.valhalla_build_admins = make_build_process()
        self.valhalla_build_admins.finished.connect(self._on_admins_finished)
        self.valhalla_build_tiles = make_build_process()
        self.valhalla_build_tiles.finished.connect(self._on_tiles_finished)

        # the build target, set when a build starts
        self._build_name = ""
        self._build_data_dir = Path()

    # ---------------- from tar (moved into the library) ----------------

    def add_tar(self):
        """Moves a plain extract tar into ``<graph_dir>/<stem>/``. A routing.earth
        managed extract is refused here — it belongs to the account flow."""
        tar_path, _ = QFileDialog.getOpenFileName(
            self._widget, "Import graph", QDir.homePath(), "Tar Files (*.tar)"
        )
        if not tar_path:
            return
        tar_path = Path(tar_path)
        if graph_registry.read_tar_state(tar_path) is not None:
            self._status_bar.pushMessage(
                f"{tar_path.name} is a routing.earth package — add it via "
                "'Update from routing.earth' instead",
                Qgis.MessageLevel.Warning,
                8,
            )
            return
        name = tar_path.stem
        if graph_registry.entry_exists(name):
            if not self._confirm_replace(name):
                return
            graph_registry.unregister(name)  # fresh dir, no stale data mixed in
        entry_dir = graph_registry.graph_dir().joinpath(name)
        entry_dir.mkdir(parents=True, exist_ok=True)
        dest = entry_dir.joinpath(tar_path.name)
        move(str(tar_path), str(dest))
        graph_registry.register(
            name,
            graph_registry.local_graph_config(tile_extract=str(dest.resolve())),
            replace=True,
        )
        self._refresh()
        self._status_bar.pushMessage(
            f"Imported {name} — tar moved into the library", Qgis.MessageLevel.Success, 3
        )

    # ---------------- from URL ----------------

    def add_url(self):
        self.from_url_dlg.exec()

    def _on_graph_add_url(self, result: QDialog.DialogCode):
        if result != QDialog.DialogCode.Accepted:
            return
        dlg = self.from_url_dlg
        name = dlg.graph_name
        if graph_registry.entry_exists(name) and not self._confirm_replace(name):
            return
        dlg.cache_dir.mkdir(parents=True, exist_ok=True)
        graph_registry.register(
            name,
            graph_registry.local_graph_config(
                tile_dir=str(dlg.cache_dir.resolve()),
                tile_url=dlg.url,
                tile_url_user_pw=dlg.user_pw,
                use_connectivity=False,
            ),
            replace=True,
        )
        self._refresh()
        self._status_bar.pushMessage(f"Added graph {name}", Qgis.MessageLevel.Success, 3)

    # ---------------- from PBF (chained builds) ----------------

    def add_pbf(self):
        self.from_pbf_dlg.open()

    def _on_graph_add_build(self, result: QDialog.DialogCode):
        if result != QDialog.DialogCode.Accepted:
            return

        if (
            self.valhalla_build_admins.state() == QProcess.ProcessState.Running
            or self.valhalla_build_tiles.state() == QProcess.ProcessState.Running
        ):
            self._status_bar.pushWarning(
                "Busy", "Other graph build is currently running, try again after it finished..."
            )
            return

        name = self.from_pbf_dlg.graph_name
        if graph_registry.entry_exists(name) and not self._confirm_replace(name):
            return
        self._build_name = name
        self._build_data_dir = self.from_pbf_dlg.data_dir
        self._build_data_dir.mkdir(parents=True, exist_ok=True)

        inline_config = {
            "mjolnir": {"admin": str(self._build_data_dir.joinpath("admins.sqlite").resolve())}
        }
        args = ["-i", json.dumps(inline_config), self.from_pbf_dlg.pbf_path]
        build_admins_exe = ValhallaSettings().get_binary_dir().joinpath("valhalla_build_admins")
        self.valhalla_build_admins.start(str(build_admins_exe.resolve()), args)
        self._status_bar.pushInfo("", "Started building admins...")
        self._log(
            f"Executing {self.valhalla_build_admins.program()} "
            f"{' '.join(self.valhalla_build_admins.arguments())}"
        )

    def _on_admins_finished(self, exit_code: int, exit_status: QProcess.ExitStatus):
        self._log(f"Finished building admins with exit code {exit_code}")
        if exit_status == QProcess.ExitStatus.CrashExit:
            self._status_bar.pushMessage(
                "Building admins failed, see log!", Qgis.MessageLevel.Critical, 0
            )
            return

        self._status_bar.pushMessage("Building admins succeeded...", Qgis.MessageLevel.Success, 0)

        inline_config = {
            "mjolnir": {
                "admin": str(self._build_data_dir.joinpath("admins.sqlite")),
                "tile_dir": str(self._build_data_dir.joinpath(self._build_name)),
                # TODO: "timezone":
            }
        }
        args = [
            "-i",
            json.dumps(inline_config),
            "-j",
            str(self.from_pbf_dlg.ui_int_threads.value() or os.cpu_count()),
            self.from_pbf_dlg.pbf_path,
        ]
        build_tiles_exe = ValhallaSettings().get_binary_dir().joinpath("valhalla_build_tiles")
        self.valhalla_build_tiles.start(str(build_tiles_exe.resolve()), args)
        self._status_bar.pushInfo("", "Started building graph tiles...")
        self._log(
            f"Executing {self.valhalla_build_tiles.program()} "
            f"{' '.join(self.valhalla_build_tiles.arguments())}"
        )

    def _on_tiles_finished(self, exit_code: int, exit_status: QProcess.ExitStatus):
        self._log(f"Finished building tiles with exit code {exit_code}")
        if exit_status == QProcess.ExitStatus.CrashExit:
            self._status_bar.pushMessage(
                "Building tiles failed, see log!", Qgis.MessageLevel.Critical, 0
            )
            return

        self._status_bar.pushMessage("Building tiles succeeded...", Qgis.MessageLevel.Success, 0)

        # TODO: produce an extract and remove tile_dir
        data_dir = self._build_data_dir.resolve()
        graph_registry.register(
            self._build_name,
            graph_registry.local_graph_config(
                tile_dir=str(data_dir.joinpath(self._build_name)),
                tile_extract=str(data_dir.joinpath(self._build_name + ".tar")),
            ),
            replace=True,  # collision was confirmed before the build started
        )
        self._refresh()

    def _on_build_log_ready(self):
        self._log(self.sender().readAll().data().decode())
