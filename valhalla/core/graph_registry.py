"""
The graph library: which graphs the plugin knows about (no UI in here).

``graph_dir`` is the single source of truth. A graph is a self-contained subdir
``<graph_dir>/<name>/`` holding ``id.json`` (the valhalla config overlay) and its
own data (tar/tiles) — nothing is tracked outside that subdir. A routing.earth
package is just an ``id.json`` that also carries a ``routing_earth`` block; its
absence marks a plain local graph.
"""

import json
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import rmtree
from typing import List, Optional

from ..gui.ui_definitions import ID_JSON
from .settings import ValhallaSettings

# the optional id.json key whose presence marks a routing-earth.com package
RE_KEY = "routing_earth"
# the identity member routing-earth-utils stores right behind index.bin
RE_STATE_MEMBER = ".routing-earth.json"


def graph_dir() -> Path:
    """The graph library root (user-relocatable); created on access."""
    d = ValhallaSettings().get_graph_dir()
    d.mkdir(exist_ok=True, parents=True)
    return d


# ---------------- timestamps ----------------


def format_dataset_timestamp(dataset_id: Optional[int]) -> str:
    """dataset_id IS the build timestamp (epoch seconds)."""
    if dataset_id is None:
        return ""
    try:
        return datetime.fromtimestamp(dataset_id, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError, TypeError):
        return str(dataset_id)


def _parse_timestamp(value) -> Optional[datetime]:
    """Epoch seconds, ISO (incl. 'Z') or our '%Y-%m-%d %H:%M' — all UTC."""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def human_size(num_bytes) -> str:
    """'4.2 GiB'-style byte formatting; '' for None/0."""
    if not num_bytes:
        return ""
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB"):
        if size < 1024:
            return f"{int(num_bytes)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def humanize_timestamp(value) -> str:
    """'4 mins ago'-style rendering; falls back to the raw value if unparsable."""
    dt = _parse_timestamp(value)
    if dt is None:
        return str(value) if value else ""
    seconds = (datetime.now(tz=timezone.utc) - dt).total_seconds()
    if seconds < 120:
        return "just now"
    for unit, unit_seconds in (("day", 86400), ("hour", 3600), ("min", 60)):
        count = int(seconds // unit_seconds)
        if count >= 1:
            return f"{count} {unit}{'s' if count > 1 else ''} ago"
    return "just now"


# ---------------- routing-earth extract identity ----------------


def read_tar_state(tar_path: Path) -> Optional[dict]:
    """
    Reads a managed extract's identity member with stdlib tarfile only
    (routing_earth_utils can never be imported in-process, see
    core/routing_earth.py). The member sits right behind ``index.bin``, so this
    is cheap even on a planet-sized tar.

    :returns: the state dict (dataset_id/scope/cadence), or None if the file is
        missing, unreadable or not a managed extract — callers flag those.
    """
    try:
        with tarfile.open(tar_path, "r") as tar:
            for _ in range(2):
                member = tar.next()
                if member is None:
                    return None
                if member.name == RE_STATE_MEMBER:
                    return json.loads(tar.extractfile(member).read())
    except (OSError, tarfile.TarError, json.JSONDecodeError):
        return None

    return None


# ---------------- entries ----------------


@dataclass
class GraphEntry:
    """One registered graph: its self-contained subdir plus parsed id.json."""

    name: str
    entry_dir: Path
    graph_config: dict  # parsed id.json (the valhalla overlay + optional RE block)
    tar_state: Optional[dict] = None  # RE extract identity, if readable

    @property
    def re_state(self) -> dict:
        """The ``routing_earth`` block (empty for plain local graphs)."""
        return self.graph_config.get(RE_KEY) or {}

    @property
    def is_re(self) -> bool:
        return RE_KEY in self.graph_config

    @property
    def is_managed(self) -> bool:
        """RE only: the tar exists and is a managed extract."""
        return self.tar_state is not None

    # -- id.json accessors --

    def _mjolnir(self, key: str) -> str:
        return (self.graph_config.get("mjolnir") or {}).get(key, "")

    @property
    def tile_extract(self) -> str:
        return self._mjolnir("tile_extract")

    @property
    def tile_dir(self) -> str:
        return self._mjolnir("tile_dir")

    @property
    def tile_url(self) -> str:
        return self._mjolnir("tile_url")

    @property
    def data_paths(self) -> List[str]:
        """The graph's on-disk data locations (all inside ``entry_dir``)."""
        return [p for p in (self.tile_extract, self.tile_dir) if p]

    # -- RE accessors (empty/None for local graphs); identity from the tar,
    #    falling back to the block when the tar is missing --

    @property
    def tar_path(self) -> Path:
        return Path(self.tile_extract)

    @property
    def scope(self) -> str:
        return (self.tar_state or self.re_state).get("scope", "")

    @property
    def cadence(self) -> str:
        return (self.tar_state or self.re_state).get("cadence", "")

    @property
    def dataset_id(self) -> Optional[int]:
        return (self.tar_state or {}).get("dataset_id")

    @property
    def built(self) -> str:
        return format_dataset_timestamp(self.dataset_id)

    @property
    def osm_data_timestamp(self) -> str:
        return self.re_state.get("osm_data_timestamp") or ""

    @property
    def synced_at(self) -> str:
        return self.re_state.get("synced_at") or ""

    @property
    def last_diff(self) -> str:
        """Human-readable size of the last downloaded diff/seed (from CLI output)."""
        return self.re_state.get("last_diff") or ""


def _load_entry(entry_dir: Path) -> Optional[GraphEntry]:
    try:
        graph_config = json.loads(entry_dir.joinpath(ID_JSON).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    entry = GraphEntry(name=entry_dir.name, entry_dir=entry_dir, graph_config=graph_config)
    if entry.is_re:
        entry.tar_state = read_tar_state(entry.tar_path)
    return entry


def list_names() -> List[str]:
    """The registered graph names — cheap, no tar reads (e.g. for combos)."""
    return sorted(
        (p.name for p in graph_dir().iterdir() if p.is_dir() and (p / ID_JSON).exists()),
        key=str.casefold,
    )


def discover() -> List[GraphEntry]:
    """All registered graphs, fully loaded (incl. RE tar identity)."""
    entries = []
    for entry_dir in sorted(graph_dir().iterdir(), key=lambda p: p.name.casefold()):
        if not entry_dir.is_dir():
            continue
        if (entry := _load_entry(entry_dir)) is not None:
            entries.append(entry)
    return entries


def entry_exists(name: str) -> bool:
    return graph_dir().joinpath(name, ID_JSON).exists()


def entry_path(name: str) -> Path:
    return graph_dir().joinpath(name)


def register(name: str, graph_config: dict, replace: bool = False) -> Path:
    """
    Writes ``<graph_dir>/<name>/id.json``. The graph data itself is placed there
    by the caller (moved/built/seeded in); this only writes the marker.

    :raises FileExistsError: name already registered and ``replace`` is False —
        callers prompt the user and re-call with ``replace=True``.
    """
    entry_dir = graph_dir().joinpath(name)
    if entry_dir.joinpath(ID_JSON).exists() and not replace:
        raise FileExistsError(str(entry_dir))
    entry_dir.mkdir(parents=True, exist_ok=True)
    entry_dir.joinpath(ID_JSON).write_text(json.dumps(graph_config, indent=2))
    return entry_dir


def set_re_state(entry_dir: Path, **fields) -> None:
    """Merges ``fields`` into the entry's ``routing_earth`` block in id.json."""
    id_json = entry_dir.joinpath(ID_JSON)
    config = json.loads(id_json.read_text())
    config.setdefault(RE_KEY, {}).update(fields)
    id_json.write_text(json.dumps(config, indent=2))


def mark_synced(entry_dir: Path, **fields) -> None:
    """Stamps ``synced_at`` (now, UTC) into the RE block alongside ``fields``."""
    set_re_state(
        entry_dir,
        synced_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
        **fields,
    )


def local_graph_config(
    tile_extract: str = "",
    tile_dir: str = "",
    tile_url: str = "",
    tile_url_user_pw: str = "",
    use_connectivity: bool = True,
) -> dict:
    """The valhalla-overlay id.json shape (absolute paths inside the entry dir)."""
    return {
        "mjolnir": {
            "tile_dir": tile_dir,
            "tile_extract": tile_extract,
            "tile_url": tile_url,
            "tile_url_user_pw": tile_url_user_pw,
        },
        "loki": {"use_connectivity": use_connectivity},
    }


def re_graph_config(tile_extract: str, scope: str, cadence: str, **state) -> dict:
    """A local config plus the ``routing_earth`` block that marks the entry RE."""
    config = local_graph_config(tile_extract=tile_extract)
    config[RE_KEY] = {"scope": scope, "cadence": cadence, **state}
    return config


def unregister(name: str) -> None:
    """Removes the whole entry subdir — metadata AND its data."""
    try:
        rmtree(graph_dir().joinpath(name))
    except OSError:
        pass
