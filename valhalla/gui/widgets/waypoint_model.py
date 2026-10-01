"""
The waypoint table's data: rows of coordinates + attributes, shown through a per-kind column
schema. A TableKind (routing, later spopt/VRP) only declares its columns, the model and the
delegate are generic.

Rows keep attributes the current schema doesn't show, so nothing gets lost when the schema
changes.
"""

from dataclasses import dataclass, field
from enum import Enum, auto, unique
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from qgis.gui import QgsDoubleSpinBox, QgsSpinBox
from qgis.PyQt.QtCore import QAbstractTableModel, QModelIndex, Qt
from qgis.PyQt.QtWidgets import QComboBox, QStyledItemDelegate, QWidget


@unique
class LocationType(str, Enum):
    BREAK = "break"
    VIA = "via"
    THROUGH = "through"
    BREAK_THROUGH = "break_through"


@unique
class PreferredSide(str, Enum):
    EITHER = "either"
    SAME = "same"
    OPPOSITE = "opposite"


class ColumnKind(Enum):
    CHOICE = auto()
    INT = auto()
    FLOAT = auto()
    TEXT = auto()
    BOOL = auto()


@dataclass(frozen=True)
class Column:
    key: str  # the attribute key in Waypoint.attrs
    header: str
    kind: ColumnKind
    default: Any
    tooltip: str = ""
    choices: Tuple[str, ...] = ()  # CHOICE only
    maximum: float = 100000  # INT/FLOAT only
    # an always-open editor, like a cell widget; costs a widget per cell, so not for big tables
    persistent: bool = False


@dataclass(frozen=True)
class TableKind:
    name: str
    columns: Tuple[Column, ...]
    ann_layer_name: str
    # the marker SVG for row i of n
    marker: Callable[[int, int, "Waypoint"], str]

    def defaults(self) -> Dict[str, Any]:
        return {c.key: c.default for c in self.columns}


@dataclass
class Waypoint:
    """A point in WGS84 with the attributes of all kinds it was edited in."""

    lon: float
    lat: float
    attrs: Dict[str, Any] = field(default_factory=dict)


def _routing_marker(row: int, n_rows: int, _: Waypoint) -> str:
    if row == 0:
        return "origin.svg"
    if row == n_rows - 1:
        return "destination.svg"
    return "via.svg"


_LOCATIONS_DOCS = "See https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/#locations"
ROUTING = TableKind(
    name="routing",
    columns=(
        Column(
            "type",
            "Type",
            ColumnKind.CHOICE,
            LocationType.BREAK.value,
            _LOCATIONS_DOCS,
            tuple(t.value for t in LocationType),
            persistent=True,
        ),
        Column(
            "preferred_side",
            "Side",
            ColumnKind.CHOICE,
            PreferredSide.EITHER.value,
            _LOCATIONS_DOCS,
            tuple(s.value for s in PreferredSide),
            persistent=True,
        ),
        Column("radius", "Radius", ColumnKind.INT, 0, "Radius in meters", persistent=True),
        Column(
            "extra",
            "Extra",
            ColumnKind.TEXT,
            "",
            "Extra location properties in URL form, e.g. 'bearing=120,20&hint=348sfj89sa' for OSRM "
            "or 'heading=120&preferred_side=same' for Valhalla",
        ),
    ),
    ann_layer_name="Valhalla Waypoints",
    marker=_routing_marker,
)


class WaypointModel(QAbstractTableModel):
    def __init__(self, kind: TableKind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._rows: List[Waypoint] = list()

    # the API for everybody but Qt

    @property
    def waypoints(self) -> List[Waypoint]:
        """The rows, in order. Don't mutate the list, use the methods below."""
        return self._rows

    def column(self, col: int) -> Column:
        return self.kind.columns[col]

    def value(self, row: int, key: str) -> Any:
        """A row's attribute, the kind's default if it was never set."""
        wp = self._rows[row]
        if key in wp.attrs:
            return wp.attrs[key]
        return next((c.default for c in self.kind.columns if c.key == key), None)

    def insert(self, row: int, waypoints: Iterable[Waypoint]):
        """Inserts before ``row``; missing attributes get the kind's defaults."""
        waypoints = list(waypoints)
        if not waypoints:
            return
        self.beginInsertRows(QModelIndex(), row, row + len(waypoints) - 1)
        for offset, wp in enumerate(waypoints):
            wp.attrs = {**self.kind.defaults(), **wp.attrs}
            self._rows.insert(row + offset, wp)
        self.endInsertRows()

    def append(self, waypoints: Iterable[Waypoint]):
        self.insert(len(self._rows), waypoints)

    def remove(self, rows: Iterable[int]):
        for row in sorted(set(rows), reverse=True):
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._rows[row]
            self.endRemoveRows()

    def move(self, row: int, delta: int) -> int:
        """Moves a row up (-1) or down (+1); returns its new position."""
        target = row + delta
        if not 0 <= row < len(self._rows) or not 0 <= target < len(self._rows):
            return row
        # Qt wants the destination as the row it ends up *before*, in pre-move indices
        dest = target + 1 if delta > 0 else target
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), dest)
        self._rows.insert(target, self._rows.pop(row))
        self.endMoveRows()
        return target

    def clear(self):
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def set_kind(self, kind: TableKind):
        """Shows the rows through another schema, their other attributes are kept."""
        self.beginResetModel()
        self.kind = kind
        for wp in self._rows:
            wp.attrs = {**kind.defaults(), **wp.attrs}
        self.endResetModel()

    # QAbstractTableModel

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.kind.columns)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if orientation != Qt.Orientation.Horizontal:
            return super().headerData(section, orientation, role)
        col = self.kind.columns[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return col.header
        if role == Qt.ItemDataRole.ToolTipRole:
            return col.tooltip or None
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        col = self.column(index.column())
        value = self.value(index.row(), col.key)
        if col.kind == ColumnKind.BOOL:
            if role == Qt.ItemDataRole.CheckStateRole:
                return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return value
        if role == Qt.ItemDataRole.ToolTipRole and col.kind == ColumnKind.TEXT:
            return value or None
        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False
        col = self.column(index.column())
        if col.kind == ColumnKind.BOOL and role == Qt.ItemDataRole.CheckStateRole:
            value = Qt.CheckState(value) == Qt.CheckState.Checked
        elif role != Qt.ItemDataRole.EditRole:
            return False
        self._rows[index.row()].attrs[col.key] = value
        self.dataChanged.emit(index, index, [role])
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.column(index.column()).kind == ColumnKind.BOOL:
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        return flags | Qt.ItemFlag.ItemIsEditable


class WaypointDelegate(QStyledItemDelegate):
    """Editors by column kind. Persistent editors commit on every change, they never close."""

    def createEditor(self, parent: QWidget, option, index: QModelIndex) -> Optional[QWidget]:
        col: Column = index.model().column(index.column())
        if col.kind == ColumnKind.CHOICE:
            editor = QComboBox(parent)
            editor.addItems(col.choices)
            editor.currentIndexChanged.connect(lambda _: self.commitData.emit(editor))
            return editor
        if col.kind in (ColumnKind.INT, ColumnKind.FLOAT):
            editor = QgsSpinBox(parent) if col.kind == ColumnKind.INT else QgsDoubleSpinBox(parent)
            editor.setMaximum(int(col.maximum) if col.kind == ColumnKind.INT else col.maximum)
            editor.valueChanged.connect(lambda _: self.commitData.emit(editor))
            return editor

        return super().createEditor(parent, option, index)

    def setEditorData(self, editor: QWidget, index: QModelIndex):
        value = index.data(Qt.ItemDataRole.EditRole)
        if isinstance(editor, QComboBox):
            editor.blockSignals(True)
            editor.setCurrentIndex(max(editor.findText(str(value)), 0))
            editor.blockSignals(False)
        elif isinstance(editor, (QgsSpinBox, QgsDoubleSpinBox)):
            editor.blockSignals(True)
            editor.setValue(value)
            editor.blockSignals(False)
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor: QWidget, model: WaypointModel, index: QModelIndex):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
        elif isinstance(editor, (QgsSpinBox, QgsDoubleSpinBox)):
            model.setData(index, editor.value())
        else:
            super().setModelData(editor, model, index)
