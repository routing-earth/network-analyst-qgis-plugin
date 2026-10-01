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
from valhalla.gui.widgets.widget_waypoints import LocationType, PreferredSide


class TestWaypointsWidget(LocalhostDockerTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Berlin
        CANVAS.setExtent(QgsRectangle(1478686, 6885333, 1500732, 6903232))
        CANVAS.setDestinationCrs(QgsCoordinateReferenceSystem.fromEpsgId(3857))

        cls.dlg = RoutingDockWidget(IFACE)

    def tearDown(self) -> None:
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
