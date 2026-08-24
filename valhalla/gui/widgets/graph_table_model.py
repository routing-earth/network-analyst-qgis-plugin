"""
The model behind the unified graphs table: registered graphs (local + RE)
first, then the "available" routing-earth entitlements not downloaded yet.
"""

from typing import List, Optional

from qgis.PyQt.QtCore import QAbstractTableModel, QModelIndex, Qt

from ...core.graph_registry import (
    GraphEntry,
    discover,
    format_dataset_timestamp,
    human_size,
    humanize_timestamp,
)
from ...core.routing_earth import Entitlement

COLUMNS = ("Region", "Cadence", "OSM age", "Last diff", "Synced", "Action")
NOT_MANAGED_TOOLTIP = (
    "Not a routing.earth managed extract (or the tar is missing) — "
    "only extracts created/synced by the routing-earth-utils client can be updated."
)
AVAILABLE_TOOLTIP = "Available on your account — not downloaded yet"
WILDCARD_TOOLTIP = "Wildcard — your account can download any region"


class GraphTableModel(QAbstractTableModel):
    """Rows = registered ``GraphEntry``s + available ``Entitlement``s."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: List[GraphEntry] = []
        self._available: List[Entitlement] = []

    # ---------------- row access ----------------

    @property
    def entries(self) -> List[GraphEntry]:
        return self._entries

    @property
    def available(self) -> List[Entitlement]:
        return self._available

    def entry_at(self, row: int) -> Optional[GraphEntry]:
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def entry_by_name(self, name: str) -> Optional[GraphEntry]:
        """Click-time re-resolution — closures must never hold stale entries."""
        return next((e for e in self._entries if e.name == name), None)

    def available_at(self, row: int) -> Optional[Entitlement]:
        i = row - len(self._entries)
        return self._available[i] if 0 <= i < len(self._available) else None

    def set_available(self, entitlements: List[Entitlement]) -> None:
        self._available = sorted(entitlements, key=lambda e: (e.scope, e.cadence))
        self.refresh()

    def refresh(self) -> None:
        """Full rescan of the registry; prunes downloaded pairs from available."""
        self.beginResetModel()
        self._entries = discover()
        local = {(e.scope, e.cadence) for e in self._entries}
        self._available = [e for e in self._available if (e.scope, e.cadence) not in local]
        self.endResetModel()

    # ---------------- QAbstractTableModel ----------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries) + len(self._available)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if (entry := self.entry_at(index.row())) is not None:
            return self._entry_data(entry, index.column(), role)
        if (ent := self.available_at(index.row())) is not None:
            return self._available_data(ent, index.column(), role)
        return None

    def _entry_data(self, entry: GraphEntry, col: int, role):
        if role == Qt.ItemDataRole.DisplayRole:
            return {
                0: entry.name,
                # TODO: derive osm & graph age for local graphs too
                1: entry.cadence if entry.is_re else "local",
                2: humanize_timestamp(entry.osm_data_timestamp),
                3: entry.last_diff,
                4: humanize_timestamp(entry.synced_at),
            }.get(col, "")
        if role == Qt.ItemDataRole.ToolTipRole:
            if entry.is_re and not entry.is_managed:
                return NOT_MANAGED_TOOLTIP
            if entry.is_re and (behind := entry.re_state.get("behind")):
                tooltip = f"Behind — latest dataset built {humanize_timestamp(int(behind))}"
                if latest_osm := entry.re_state.get("latest_osm"):
                    tooltip += f", OSM data {latest_osm}"
                return tooltip
            # exact timestamps for the age columns, data paths otherwise;
            # the graph build date (dataset_id) lives on the diff column
            exact = {
                2: entry.osm_data_timestamp,
                3: f"graph built {entry.built}" if entry.built else "",
                4: entry.synced_at,
            }.get(col, "")
            if exact:
                return f"{exact} UTC"
            return "\n".join(entry.data_paths + ([entry.tile_url] if entry.tile_url else []))
        if role == Qt.ItemDataRole.ForegroundRole and entry.is_re and not entry.is_managed:
            return Qt.GlobalColor.red
        return None

    def _available_data(self, ent: Entitlement, col: int, role):
        if role == Qt.ItemDataRole.DisplayRole:
            return {
                0: ent.scope,
                1: ent.cadence,
                2: humanize_timestamp(ent.osm_data_timestamp),
                # the "Last diff" column doubles as the download size here
                3: human_size(ent.compressed_size_bytes),
            }.get(col, "")
        if role == Qt.ItemDataRole.ToolTipRole:
            if ent.scope == "*":
                return WILDCARD_TOOLTIP
            parts = [AVAILABLE_TOOLTIP]
            if disk := human_size(ent.size_bytes):
                parts.append(f"{disk} on disk")
            if ent.dataset_id:
                parts.append(f"built {format_dataset_timestamp(ent.dataset_id)} UTC")
            return " — ".join(parts)
        if role == Qt.ItemDataRole.ForegroundRole:
            return Qt.GlobalColor.gray
        return None
