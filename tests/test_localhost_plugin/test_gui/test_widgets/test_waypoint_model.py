import unittest

from qgis.PyQt.QtCore import Qt, qInstallMessageHandler
from qgis.PyQt.QtTest import QAbstractItemModelTester
from qgis.PyQt.QtWidgets import QComboBox, QTableView
from tests.utilities import get_qgis_app

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.gui.widgets.waypoint_model import (  # noqa: E402
    ROUTING,
    SPOPT,
    Column,
    ColumnKind,
    Waypoint,
    WaypointDelegate,
    WaypointTableKind,
    WaypointTableModel,
)

OTHER = WaypointTableKind(
    name="other",
    columns=(
        Column("type", "Type", ColumnKind.CHOICE, "a", choices=("a", "b"), persistent=True),
        Column("flag", "Flag", ColumnKind.BOOL, False),
    ),
    ann_layer_name="other",
    marker=lambda *_: "via.svg",
)


class TestWaypointModel(unittest.TestCase):
    """Pure model tests, no valhalla needed."""

    def setUp(self):
        # the tester reports through qWarning: collect them instead of crashing with Fatal
        self.tester_failures = list()
        self.prev_handler = qInstallMessageHandler(
            lambda _, __, msg: self.tester_failures.append(msg) if msg.startswith("FAIL!") else None
        )
        self.model = WaypointTableModel(ROUTING)
        # checks every signal and index for consistency
        self.tester = QAbstractItemModelTester(
            self.model, QAbstractItemModelTester.FailureReportingMode.Warning
        )
        self.model.append(Waypoint(float(i), float(i)) for i in range(3))

    def tearDown(self):
        qInstallMessageHandler(self.prev_handler)
        self.assertEqual(self.tester_failures, [])

    def lons(self):
        return [wp.lon for wp in self.model.waypoints]

    def test_defaults(self):
        self.assertEqual(self.model.rowCount(), 3)
        self.assertEqual(self.model.columnCount(), len(ROUTING.columns))
        self.assertEqual(self.model.waypoints[0].attrs, ROUTING.defaults())
        self.assertEqual(self.model.data(self.model.index(0, 2)), 0)  # radius
        self.assertEqual(self.model.headerData(0, Qt.Orientation.Horizontal), "Type")

    def test_insert_keeps_given_attrs(self):
        self.model.insert(1, [Waypoint(9.0, 9.0, {"radius": 50})])
        self.assertEqual(self.lons(), [0, 9, 1, 2])
        self.assertEqual(self.model.value(1, "radius"), 50)
        self.assertEqual(self.model.value(1, "type"), "break")

    def test_move(self):
        self.assertEqual(self.model.move(0, 1), 1)
        self.assertEqual(self.lons(), [1, 0, 2])
        self.assertEqual(self.model.move(2, -1), 1)
        self.assertEqual(self.lons(), [1, 2, 0])
        # out of bounds is a no-op
        self.assertEqual(self.model.move(0, -1), 0)
        self.assertEqual(self.model.move(2, 1), 2)
        self.assertEqual(self.lons(), [1, 2, 0])

    def test_remove_and_clear(self):
        self.model.remove([2, 0])
        self.assertEqual(self.lons(), [1])
        self.model.clear()
        self.assertEqual(self.model.rowCount(), 0)

    def test_set_data(self):
        idx = self.model.index(1, 0)
        self.assertTrue(self.model.setData(idx, "via"))
        self.assertEqual(self.model.waypoints[1].attrs["type"], "via")

    def test_set_kind_keeps_attrs(self):
        """Switching the schema back and forth loses nothing."""
        self.model.setData(self.model.index(0, 2), 100)  # radius
        self.model.set_kind(OTHER)
        self.assertEqual(self.model.columnCount(), 2)
        self.assertEqual(self.model.value(0, "flag"), False)

        flag = self.model.index(0, 1)
        self.assertTrue(self.model.flags(flag) & Qt.ItemFlag.ItemIsUserCheckable)
        self.model.setData(flag, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
        self.assertEqual(self.model.data(flag, Qt.ItemDataRole.CheckStateRole), Qt.CheckState.Checked)

        self.model.set_kind(ROUTING)
        self.assertEqual(self.model.value(0, "radius"), 100)
        self.assertTrue(self.model.waypoints[0].attrs["flag"])

    def test_persistent_editor_commits(self):
        """A persistent combo writes through to the model on every change."""
        view = QTableView()
        view.setModel(self.model)
        view.setItemDelegate(WaypointDelegate(view))
        idx = self.model.index(0, 0)
        view.openPersistentEditor(idx)
        combo: QComboBox = view.indexWidget(idx)
        self.assertEqual(combo.currentText(), "break")

        combo.setCurrentText("through")
        self.assertEqual(self.model.value(0, "type"), "through")

        # and the other way around: model changes show up in the editor
        self.model.setData(idx, "via")
        self.assertEqual(combo.currentText(), "via")

    def test_spopt_applicability(self):
        """Weight is for demand points, predefined for facilities; the role switches them."""
        self.model.set_kind(SPOPT)
        keys = [c.key for c in SPOPT.columns]
        role, weight, predefined = (
            self.model.index(0, keys.index(k)) for k in ("role", "weight", "predefined")
        )

        # a demand point by default
        self.assertEqual(self.model.data(role), "demand")
        self.assertEqual(self.model.data(weight), 1.0)
        self.assertIsNone(self.model.data(predefined, Qt.ItemDataRole.CheckStateRole))
        self.assertEqual(self.model.flags(predefined), Qt.ItemFlag.ItemIsSelectable)

        self.model.setData(role, "facility")
        self.assertIsNone(self.model.data(weight))
        self.assertFalse(self.model.flags(weight) & Qt.ItemFlag.ItemIsEditable)
        self.assertTrue(self.model.flags(predefined) & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(
            self.model.data(predefined, Qt.ItemDataRole.CheckStateRole), Qt.CheckState.Unchecked
        )
