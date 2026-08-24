import unittest

from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest

from valhalla.core.settings import ValhallaSettings, get_settings_dir

from ...constants import WAYPOINTS_4326
from ...utilities import get_qgis_app

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.global_definitions import DEFAULT_LAYER_FIELDS, RouterEndpoint, RouterType
from valhalla.gui.dock_routing import RoutingDockWidget
from valhalla.utils.resource_utils import get_default_valhalla_binary_dir


class TestRoutingDialog(unittest.TestCase):
    """Tests the main routing dialog"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dlg = RoutingDockWidget(IFACE)

        # set localhost instead of FOSSGIS
        cls.dlg.router_widget.ui_cmb_prov.setCurrentIndex(1)

        cls.iso_layer: QgsVectorLayer = cls.dlg._get_output_layer(
            RouterEndpoint.ISOCHRONES,
            locations=WAYPOINTS_4326,
            params={"intervals": [20], "polygons": True},
        )
        cls.route_layer: QgsVectorLayer = cls.dlg._get_output_layer(
            RouterEndpoint.DIRECTIONS,
            locations=WAYPOINTS_4326[:2],
            params={},
        )
        cls.matrix_layer: QgsVectorLayer = cls.dlg._get_output_layer(
            RouterEndpoint.MATRIX,
            locations=WAYPOINTS_4326,
            params={},
        )

    def tearDown(self) -> None:
        QgsProject.instance().removeAllMapLayers()

    def test_layer_name(self):
        self.assertEqual(
            self.iso_layer.name(),
            "Valhalla Isochrones Pedestrian",
        )

        self.assertEqual(
            self.matrix_layer.name(),
            "Valhalla Matrix Pedestrian",
        )

    def test_layer_fields(self):
        iso_fields = [f for f in self.iso_layer.fields()]
        self.assertEqual(iso_fields, list(DEFAULT_LAYER_FIELDS[RouterEndpoint.ISOCHRONES]))

        matrix_fields = [f for f in self.matrix_layer.fields()]
        self.assertEqual(matrix_fields, list(DEFAULT_LAYER_FIELDS[RouterEndpoint.MATRIX]))

    def test_provider_change(self):
        """
        Tests whether UI elements are successfully
        disabled/enabled for specific provider/endpoint combinations.
        """

        # Valhalla – Expansion
        self.dlg.router_widget.ui_cmb_prov.setCurrentIndex(0)
        self.assertTrue(self.dlg.waypoints_widget.isEnabled())
        self.assertTrue(self.dlg.ui_valhalla_expansion_params.isEnabled())
        self.assertTrue(self.dlg.execute_btn.isEnabled())

        # Valhalla – Isochrones
        self.dlg.menu_widget.setCurrentRow(1)
        self.assertTrue(self.dlg.waypoints_widget.isEnabled())
        self.assertTrue(self.dlg.ui_valhalla_isochrones_params.isEnabled())
        self.assertTrue(self.dlg.execute_btn.isEnabled())

    def test_first_start_ever(self):
        # first remove the settings ini
        settings_ini = get_settings_dir().joinpath("settings.ini")
        if settings_ini.exists():
            settings_ini.unlink()

        RoutingDockWidget(IFACE)

        self.assertTrue(settings_ini.exists())
        self.assertEqual(len(ValhallaSettings().get_providers(RouterType.VALHALLA)), 2)
        self.assertTrue(get_settings_dir().joinpath("graphs").is_dir())
        self.assertEqual(ValhallaSettings().get_binary_dir(), get_default_valhalla_binary_dir())

    def test_graph_extent_fossgis(self):
        # set FOSSGIS, so we get an info msg
        self.dlg.router_widget.ui_cmb_prov.setCurrentIndex(0)
        QTest.mouseClick(self.dlg.ui_graph_btn, Qt.MouseButton.LeftButton)
        self.assertIn(
            "The public server has the full world graph and admins/timezones loaded",
            self.dlg.status_bar.currentItem().text(),
        )

    def test_graph_extent_localhost(self):
        self.dlg.router_widget.ui_cmb_prov.setCurrentIndex(1)
        QTest.mouseClick(self.dlg.ui_graph_btn, Qt.MouseButton.LeftButton)
        self.assertIn(
            "Both admin areas and timezones are built into the graph.",
            self.dlg.status_bar.currentItem().text(),
        )

        layers = list(QgsProject.instance().mapLayers().values())
        layer: QgsVectorLayer = layers[0]
        self.assertEqual(len(layers), 1)
        self.assertTrue(
            layer.geometryType() == QgsWkbTypes.GeometryType.PolygonGeometry,
            msg=f"Extent layer has unexpected Geometry Type {layer.geometryType()}",
        )
        self.assertEqual(layer.featureCount(), 1)
