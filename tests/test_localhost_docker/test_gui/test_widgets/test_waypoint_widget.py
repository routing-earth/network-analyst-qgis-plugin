import json
from tempfile import NamedTemporaryFile
from time import sleep
from urllib.parse import unquote, urlencode

from qgis.core import (
    QgsAnnotationLayer,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsLayerTreeNode,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas, QgsMapMouseEvent
from qgis.PyQt.QtCore import QEvent, QPoint, Qt, QTimer
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication, QDialogButtonBox

from .... import LocalhostDockerTestCase
from ....constants import WAYPOINTS_3857, WAYPOINTS_4326
from ....utilities import assertQueryStringEqual, get_qgis_app

CANVAS: QgsMapCanvas
QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.global_definitions import RouterType
from valhalla.gui.dlg_from_json import FromValhallaJsonDialog
from valhalla.gui.dlg_from_lyr import FromLayerDialog
from valhalla.gui.dlg_from_osrm_url import FromOsrmUrlDialog
from valhalla.gui.dock_routing import RoutingDockWidget
from valhalla.gui.widgets.waypoint_model import ROUTING, SPOPT
from valhalla.gui.widgets.widget_waypoints import PROJECT_SCOPE, LocationType, PreferredSide


class TestWaypointsWidget(LocalhostDockerTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Berlin
        CANVAS.setExtent(QgsRectangle(1478686, 6885333, 1500732, 6903232))
        CANVAS.setDestinationCrs(QgsCoordinateReferenceSystem.fromEpsgId(3857))

        cls.dlg = RoutingDockWidget(IFACE)

    def tearDown(self) -> None:
        for kind in (SPOPT, ROUTING):
            self.dlg.waypoints_widget.set_kind(kind)
            self.dlg.waypoints_widget._handle_clear_locations()
        QgsProject.instance().removeAllMapLayers()

    def add_waypoints(self, points):
        """adds waypoints to the table"""
        # click the add button which should hide the dialog
        QTest.mouseClick(self.dlg.waypoints_widget.ui_btn_add_pt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.dlg.isVisible())
        sleep(0.2)

        # add 3 points in Berlin
        for pt in points:
            self.dlg.waypoints_widget.point_tool.canvasClicked.emit(
                QgsPointXY(*pt), Qt.MouseButton.LeftButton
            )
            sleep(0.1)

        # finish collecting points with double click
        double_click = QgsMapMouseEvent(
            CANVAS,
            QEvent.Type.MouseButtonDblClick,
            QPoint(0, 0),  # Relative to the canvas' dimensions
            Qt.MouseButton.LeftButton,
        )
        self.dlg.waypoints_widget.point_tool.canvasDoubleClickEvent(double_click)
        self.assertTrue(self.dlg.isVisible())

    def test_get_valhalla_locations(self):
        self.dlg.setVisible(True)

        types = [LocationType.BREAK_THROUGH, LocationType.THROUGH, LocationType.VIA]
        sides = [PreferredSide.EITHER, PreferredSide.SAME, PreferredSide.OPPOSITE]
        radiuses = [0, 100, 1000]
        extra_params = [
            {
                "rank_candidates": True,
                "heading": 120,
                "display_lat": 5.32,
            },
            {
                "rank_candidates": False,
                "heading": 10,
                "display_lat": 3.51,
            },
            {
                "rank_candidates": True,
                "heading": 12,
                "display_lat": 3.12,
            },
        ]

        # add the points to the table
        for row_id, pt in enumerate(WAYPOINTS_4326):
            self.dlg.waypoints_widget.add_waypoint(
                pt[0],
                pt[1],
                type=types[row_id],
                preferred_side=sides[row_id],
                radius=radiuses[row_id],
                extra=unquote(urlencode(extra_params[row_id])),
            )

        for idx, loc in enumerate(self.dlg.waypoints_widget.get_locations(RouterType.VALHALLA)):
            expected = {
                "lon": WAYPOINTS_4326[idx][0],
                "lat": WAYPOINTS_4326[idx][1],
                **extra_params[idx],
            }
            if radiuses[idx]:
                expected["radius"] = radiuses[idx]
            expected["type"] = types[idx]
            expected["preferred_side"] = sides[idx]
            self.assertDictEqual(expected, loc._make_waypoint())

    def test_add_waypoints(self):
        table = self.dlg.waypoints_widget.ui_table
        self.dlg.setVisible(True)
        self.add_waypoints(WAYPOINTS_3857)

        # we have 3 coordinates in there and they're properly projected from 3857 to 4326
        self.assertEqual(table.model().rowCount(), 3)
        waypoints = self.dlg.waypoints_widget.waypoints
        for row_id in range(table.model().rowCount()):
            self.assertAlmostEqual(waypoints[row_id].lon, WAYPOINTS_4326[row_id][0], 5)
            self.assertAlmostEqual(waypoints[row_id].lat, WAYPOINTS_4326[row_id][1], 5)

    def test_remove_waypoints(self):
        table = self.dlg.waypoints_widget.ui_table
        self.dlg.setVisible(True)
        self.add_waypoints(WAYPOINTS_3857)

        # select the first point and remove it
        table.selectRow(0)
        QTest.mouseClick(self.dlg.waypoints_widget.ui_btn_rm_pt, Qt.MouseButton.LeftButton)
        self.assertEqual(table.model().rowCount(), 2)

        # make sure it's the actually the first one that was removed
        first_pt = self.dlg.waypoints_widget.waypoints[0]
        self.assertAlmostEqual(first_pt.lon, WAYPOINTS_4326[1][0], 5)
        self.assertAlmostEqual(first_pt.lat, WAYPOINTS_4326[1][1], 5)

    def test_clear_all_waypoints(self):
        table = self.dlg.waypoints_widget.ui_table
        self.dlg.setVisible(True)
        self.add_waypoints(WAYPOINTS_3857)

        self.assertEqual(table.model().rowCount(), 3)
        QTest.mouseClick(self.dlg.waypoints_widget.ui_btn_rm_all, Qt.MouseButton.LeftButton)
        self.assertEqual(table.model().rowCount(), 0)

    def test_move_item_up(self):
        table = self.dlg.waypoints_widget.ui_table
        self.dlg.setVisible(True)
        self.add_waypoints(WAYPOINTS_3857)

        # remember the old configuration before moving rows
        waypoints = self.dlg.waypoints_widget.waypoints
        old_first, old_second = waypoints[0], waypoints[1]

        table.selectRow(1)
        QTest.mouseClick(self.dlg.waypoints_widget.ui_btn_up, Qt.MouseButton.LeftButton)

        self.assertIs(old_first, waypoints[1])
        self.assertIs(old_second, waypoints[0])
        self.assertEqual(table.currentIndex().row(), 0)

    def test_move_item_down(self):
        table = self.dlg.waypoints_widget.ui_table
        self.dlg.setVisible(True)
        self.add_waypoints(WAYPOINTS_3857)

        # remember the old configuration before moving rows
        waypoints = self.dlg.waypoints_widget.waypoints
        old_first, old_second = waypoints[1], waypoints[2]

        table.selectRow(1)
        QTest.mouseClick(self.dlg.waypoints_widget.ui_btn_down, Qt.MouseButton.LeftButton)

        self.assertIs(old_first, waypoints[2])
        self.assertIs(old_second, waypoints[1])
        self.assertEqual(table.currentIndex().row(), 2)

    def test_from_layer(self):
        self.dlg.setVisible(True)
        # First add a point layer
        pt_lyr = QgsVectorLayer("Point?crs=EPSG:3857", "single_point", "memory")
        for point in WAYPOINTS_3857:
            feat = QgsFeature()
            feat.setGeometry(QgsPoint(*point))
            pt_lyr.dataProvider().addFeature(feat)
        QgsProject.instance().addMapLayer(pt_lyr)

        # function to set the right layer
        def handle_exec():
            dlg: FromLayerDialog = QApplication.activeWindow()
            self.assertIsInstance(dlg, FromLayerDialog)
            # the layer we added will automatically be chosen
            QTest.mouseClick(
                dlg.buttonBox.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton
            )

        # then press the button and set the right layer when it's open
        QTimer.singleShot(100, handle_exec)
        self.dlg.waypoints_widget._handle_from_layer()

        # test we got 3 points and they were properly transformed to 4326
        table = self.dlg.waypoints_widget.ui_table
        self.assertEqual(table.model().rowCount(), 3)
        waypoints = self.dlg.waypoints_widget.waypoints
        for row_id in range(table.model().rowCount()):
            self.assertAlmostEqual(waypoints[row_id].lon, WAYPOINTS_4326[row_id][0], 5)
            self.assertAlmostEqual(waypoints[row_id].lat, WAYPOINTS_4326[row_id][1], 5)

    def test_from_valhalla_json(self):
        self.dlg.setVisible(True)

        extra_params = {
            "rank_candidates": True,
            "heading": 120,
            "display_lat": 5.32,
        }

        # mock up the JSON we'll inject into the dialog
        # keep one example of each extra data type
        locs_json = list()
        for pt in WAYPOINTS_4326:
            locs_json.append(
                {
                    "lon": pt[0],
                    "lat": pt[1],
                    "radius": 10,
                    "preferred_side": "same",
                    "type": "via",
                    **extra_params,
                }
            )

        # function to set the right layer
        def handle_exec(input_json):
            dlg: FromValhallaJsonDialog = QApplication.activeWindow()
            self.assertIsInstance(dlg, FromValhallaJsonDialog)
            dlg.json_field.setText(json.dumps(input_json))
            QTest.mouseClick(
                dlg.buttonBox.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton
            )

        # then press the button and set the right layer when it's open
        QTimer.singleShot(100, lambda: handle_exec(locs_json))
        self.dlg.waypoints_widget._handle_from_valhalla_json()

        # test we got 3 points and they were properly transformed to 4326
        table = self.dlg.waypoints_widget.ui_table
        self.assertEqual(self.dlg.waypoints_widget.ui_table.model().rowCount(), 3)
        waypoints = self.dlg.waypoints_widget.waypoints
        for row_id in range(table.model().rowCount()):
            self.assertEqual(waypoints[row_id].attrs["type"], "via")
            self.assertEqual(waypoints[row_id].attrs["preferred_side"], "same")
            self.assertEqual(waypoints[row_id].attrs["radius"], 10)
            assertQueryStringEqual(waypoints[row_id].attrs["extra"], unquote(urlencode(extra_params)))
            self.assertAlmostEqual(waypoints[row_id].lon, WAYPOINTS_4326[row_id][0], 5)
            self.assertAlmostEqual(waypoints[row_id].lat, WAYPOINTS_4326[row_id][1], 5)

        # try the same with a full valhalla request json
        self.dlg.waypoints_widget._handle_clear_locations()
        full_json = {
            "locations": locs_json,
            "costing": "auto",
            "costing_options": {"auto": {"use_tolls": 0.2}},
        }
        QTimer.singleShot(100, lambda: handle_exec(full_json))
        self.dlg.waypoints_widget._handle_from_valhalla_json()

        # test we got 3 points and they were properly transformed to 4326
        self.assertEqual(self.dlg.waypoints_widget.ui_table.model().rowCount(), 3)
        waypoints = self.dlg.waypoints_widget.waypoints
        for row_id in range(table.model().rowCount()):
            self.assertEqual(waypoints[row_id].attrs["type"], "via")
            self.assertEqual(waypoints[row_id].attrs["preferred_side"], "same")
            self.assertEqual(waypoints[row_id].attrs["radius"], 10)
            assertQueryStringEqual(waypoints[row_id].attrs["extra"], unquote(urlencode(extra_params)))
            self.assertAlmostEqual(waypoints[row_id].lon, WAYPOINTS_4326[row_id][0], 5)
            self.assertAlmostEqual(waypoints[row_id].lat, WAYPOINTS_4326[row_id][1], 5)

    def test_from_osrm_url(self):
        self.dlg.setVisible(True)

        radiuses = ("100", "0", "100")
        bearings = ("1,2", "3,5", "10,5")

        query_params = {"bearings": ";".join(bearings), "radiuses": ";".join(radiuses)}
        locations = list()
        for pt in WAYPOINTS_4326:
            locations.append(f"{pt[0]},{pt[1]}")
        locations_str = ";".join(locations)

        url = (
            f"https://routing.openstreetmap.de/routed-bike/route/v1/driving/"
            f"{locations_str}?"
            f"{urlencode(query_params)}"
        )

        # function to set the right layer
        def handle_exec():
            dlg: FromOsrmUrlDialog = QApplication.activeWindow()
            self.assertIsInstance(dlg, FromOsrmUrlDialog)
            dlg.ui_url.setText(url)
            QTest.mouseClick(
                dlg.buttonBox.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton
            )

        # then press the button and set the right layer when it's open
        QTimer.singleShot(100, handle_exec)
        self.dlg.waypoints_widget._handle_from_osrm_url()

        # test we got 3 points and they were properly transformed to 4326
        table = self.dlg.waypoints_widget.ui_table
        self.assertEqual(self.dlg.waypoints_widget.ui_table.model().rowCount(), 3)
        waypoints = self.dlg.waypoints_widget.waypoints
        for row_id in range(table.model().rowCount()):
            self.assertEqual(waypoints[row_id].attrs["type"], "break")
            self.assertEqual(waypoints[row_id].attrs["preferred_side"], "either")
            self.assertEqual(waypoints[row_id].attrs["radius"], int(radiuses[row_id]))
            assertQueryStringEqual(waypoints[row_id].attrs["extra"], f"heading={bearings[row_id]}")
            self.assertAlmostEqual(waypoints[row_id].lon, WAYPOINTS_4326[row_id][0], 5)
            self.assertAlmostEqual(waypoints[row_id].lat, WAYPOINTS_4326[row_id][1], 5)

    def test_waypoints_layer(self):
        self.dlg.setVisible(True)
        self.add_waypoints(WAYPOINTS_4326)

        ann_lyr: QgsAnnotationLayer = QgsProject.instance().mapLayersByName(
            self.dlg.waypoints_widget.ANN_NAME
        )[0]
        items = ann_lyr.items()
        self.assertEqual(len(items), 3)

    def test_waypoints_layer_visibility(self):
        self.dlg.setVisible(True)
        self.assertEqual(len(QgsProject.instance().mapLayers()), 0)
        self.add_waypoints(WAYPOINTS_4326)

        # test if the visibility signal fires
        points_node: QgsLayerTreeNode = (
            QgsProject.instance().layerTreeRoot().findLayer(self.dlg.waypoints_widget.points_lyr_id)
        )

        points_node.setItemVisibilityChecked(True)
        self.assertTrue(self.dlg.waypoints_widget.ui_btn_show_point_lyr.isChecked())

        points_node.setItemVisibilityChecked(False)
        self.assertFalse(self.dlg.waypoints_widget.ui_btn_show_point_lyr.isChecked())

        points_node.setItemVisibilityChecked(True)
        self.assertTrue(self.dlg.waypoints_widget.ui_btn_show_point_lyr.isChecked())

        # then check the other way around: hit the button and check visibility
        QTest.mouseClick(self.dlg.waypoints_widget.ui_btn_show_point_lyr, Qt.MouseButton.LeftButton)
        self.assertFalse(points_node.isVisible())
        QTest.mouseClick(self.dlg.waypoints_widget.ui_btn_show_point_lyr, Qt.MouseButton.LeftButton)
        self.assertTrue(points_node.isVisible())

    def test_waypoints_table_preserves_itself(self):
        # first add some points to the project's annotation layer
        self.dlg.setVisible(True)

        with NamedTemporaryFile(suffix=".qgz") as p1, NamedTemporaryFile(suffix=".qgz") as p2:
            # first write a project with 3 coords, clear the table, then write a project with 2 coords
            # write to project with all waypoints intact
            self.add_waypoints(WAYPOINTS_4326)
            self.assertEqual(self.dlg.waypoints_widget.ui_table.model().rowCount(), 3)
            QgsProject.instance().write(p1.name)

            self.dlg.waypoints_widget._handle_clear_locations()

            self.add_waypoints(WAYPOINTS_3857[:2])
            self.assertEqual(self.dlg.waypoints_widget.ui_table.model().rowCount(), 2)
            QgsProject.instance().write(p2.name)

            # now open one after the other and check that the table was updated accordingly
            QgsProject.instance().read(p1.name)
            self.assertEqual(self.dlg.waypoints_widget.ui_table.model().rowCount(), 3)

            QgsProject.instance().read(p2.name)
            self.assertEqual(self.dlg.waypoints_widget.ui_table.model().rowCount(), 2)

    def test_kinds_keep_their_points(self):
        """Every table kind has its own points and annotation layer."""
        widget = self.dlg.waypoints_widget
        self.dlg.setVisible(True)
        self.add_waypoints(WAYPOINTS_3857[:1])

        widget.set_kind(SPOPT)
        self.assertEqual(widget.model.rowCount(), 0)
        self.assertEqual(widget.model.columnCount(), len(SPOPT.columns))
        self.add_waypoints(WAYPOINTS_3857)
        self.assertEqual([wp.attrs["role"] for wp in widget.waypoints], ["demand"] * 3)

        # both layers exist, only the shown kind's is visible
        root = QgsProject.instance().layerTreeRoot()
        spopt_lyr = QgsProject.instance().mapLayersByName(SPOPT.ann_layer_name)[0]
        routing_lyr = QgsProject.instance().mapLayersByName(ROUTING.ann_layer_name)[0]
        self.assertEqual(len(spopt_lyr.items()), 3)
        self.assertFalse(root.findLayer(routing_lyr.id()).isVisible())

        widget.set_kind(ROUTING)
        self.assertEqual(widget.model.rowCount(), 1)
        self.assertTrue(root.findLayer(routing_lyr.id()).isVisible())
        self.assertFalse(root.findLayer(spopt_lyr.id()).isVisible())
        # the routing requests only ever see routing points
        self.assertEqual(len(widget.get_locations(RouterType.VALHALLA)), 1)

    def test_add_modes(self):
        """The add button's menu picks what the clicked points become."""
        widget = self.dlg.waypoints_widget
        self.dlg.setVisible(True)
        widget.set_kind(SPOPT)
        add_facilities = [
            a for a in widget.ui_btn_add_pt.menu().actions() if a.text() == "Add facilities"
        ]
        add_facilities[0].trigger()
        self.assertTrue(widget.ui_btn_add_pt.isChecked())
        widget.point_tool.canvasClicked.emit(QgsPointXY(*WAYPOINTS_3857[0]), Qt.MouseButton.LeftButton)
        widget._handle_doubleclick()
        self.assertEqual(widget.waypoints[0].attrs["role"], "facility")

        # routing has no modes
        widget.set_kind(ROUTING)
        self.assertIsNone(widget.ui_btn_add_pt.menu())

    def test_points_saved_in_project(self):
        """All kinds' points and attributes round-trip through the project file."""
        widget = self.dlg.waypoints_widget
        self.dlg.setVisible(True)
        widget.add_waypoint(*WAYPOINTS_4326[0], radius=42)
        widget.set_kind(SPOPT)
        widget.add_waypoint(*WAYPOINTS_4326[1], role="facility", name="depot", predefined=True)
        widget.add_waypoint(*WAYPOINTS_4326[2], weight=7.5)

        with NamedTemporaryFile(suffix=".qgz") as project_file:
            QgsProject.instance().write(project_file.name)
            for kind in (SPOPT, ROUTING):
                widget.set_kind(kind)
                widget._handle_clear_locations()
            QgsProject.instance().read(project_file.name)

        self.assertEqual(widget.models[ROUTING.name].waypoints[0].attrs["radius"], 42)
        facility, demand = widget.models[SPOPT.name].waypoints
        self.assertEqual(
            (facility.attrs["role"], facility.attrs["name"], facility.attrs["predefined"]),
            ("facility", "depot", True),
        )
        self.assertEqual((demand.attrs["role"], demand.attrs["weight"]), ("demand", 7.5))
        self.assertAlmostEqual(demand.lon, WAYPOINTS_4326[2][0])

    def test_old_project_from_annotations(self):
        """A project from before the points were stored: routing comes from its markers."""
        widget = self.dlg.waypoints_widget
        self.dlg.setVisible(True)
        self.add_waypoints(WAYPOINTS_3857)
        QgsProject.instance().removeEntry(PROJECT_SCOPE, "waypoints")

        widget._handle_read_project()
        self.assertEqual(widget.model.rowCount(), 3)
        # an annotation layer doesn't keep the order: the reason the points are stored now
        restored = sorted((wp.lon, wp.lat) for wp in widget.waypoints)
        for (lon, lat), (exp_lon, exp_lat) in zip(restored, sorted(WAYPOINTS_4326)):
            self.assertAlmostEqual(lon, exp_lon, 5)
            self.assertAlmostEqual(lat, exp_lat, 5)

    def test_new_project_clears(self):
        widget = self.dlg.waypoints_widget
        self.dlg.setVisible(True)
        widget.add_waypoint(*WAYPOINTS_4326[0])
        QgsProject.instance().clear()
        # a cleared project resets the canvas CRS the other tests rely on
        CANVAS.setDestinationCrs(QgsCoordinateReferenceSystem.fromEpsgId(3857))
        self.assertEqual(widget.model.rowCount(), 0)

    def test_from_layer_spopt(self):
        """A layer import as demand points or facilities, with fields mapped to columns."""
        widget = self.dlg.waypoints_widget
        self.dlg.setVisible(True)
        widget.set_kind(SPOPT)
        pt_lyr = QgsVectorLayer(
            "Point?crs=EPSG:3857&field=label:string&field=pop:double&field=fixed:integer",
            "pts",
            "memory",
        )
        for i, point in enumerate(WAYPOINTS_3857):
            feat = QgsFeature(pt_lyr.fields())
            feat.setGeometry(QgsPoint(*point))
            feat.setAttributes([f"pt{i}", float(i * 10), i % 2])
            pt_lyr.dataProvider().addFeature(feat)
        QgsProject.instance().addMapLayer(pt_lyr)

        def import_as(mode_idx: int, mapping: dict):
            def handle_exec():
                dlg: FromLayerDialog = QApplication.activeWindow()
                dlg.mode_combo.setCurrentIndex(mode_idx)
                for key, field_name in mapping.items():
                    dlg.field_combos[key].setField(field_name)
                QTest.mouseClick(
                    dlg.buttonBox.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton
                )

            QTimer.singleShot(100, handle_exec)
            widget._handle_from_layer()

        import_as(0, {"name": "label", "weight": "pop"})  # demand points
        import_as(1, {"predefined": "fixed"})  # facilities
        demand, facilities = widget.waypoints[:3], widget.waypoints[3:]
        self.assertEqual([wp.attrs["role"] for wp in demand], ["demand"] * 3)
        self.assertEqual([wp.attrs["name"] for wp in demand], ["pt0", "pt1", "pt2"])
        self.assertEqual([wp.attrs["weight"] for wp in demand], [0.0, 10.0, 20.0])
        self.assertEqual([wp.attrs["role"] for wp in facilities], ["facility"] * 3)
        self.assertEqual([wp.attrs["predefined"] for wp in facilities], [False, True, False])
        self.assertAlmostEqual(facilities[1].lat, WAYPOINTS_4326[1][1], 5)

        # the routing-only imports are hidden for spopt
        self.assertFalse(any(a.isVisible() for a in widget._routing_import_actions))
