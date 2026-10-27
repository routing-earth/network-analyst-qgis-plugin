from typing import List, Optional

from qgis.core import QgsField, QgsMapLayer, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt.QtCore import QEventLoop, QTimer, QVariant

from valhalla.core.spopt import SpoptProblem
from valhalla.global_definitions import FieldNames
from valhalla.gui.spopt_run import SpoptRun, points_layer, split_waypoints
from valhalla.gui.widgets.waypoint_model import Waypoint

from ... import clear_project_points
from ..test_processing.spopt_base import SpoptProcessingBase, load_geojson


def matrix_by_index() -> QgsVectorLayer:
    """tests/data/matrix.geojson with source/target as the points' 0-based index, like the dock's."""
    src = load_geojson("matrix")
    layer = QgsVectorLayer("None", "matrix", "memory")
    layer.dataProvider().addAttributes(src.fields().toList())
    layer.updateFields()
    feats = list()
    for feat in src.getFeatures():
        feat[FieldNames.SOURCE] -= 1
        feat[FieldNames.TARGET] -= 1
        feats.append(feat)
    layer.dataProvider().addFeatures(feats)
    return layer


class TestSpoptRun(SpoptProcessingBase):
    """The dock's background run, on the static matrix (no valhalla)."""

    def setUp(self):
        clear_project_points()
        # the table's points: the fixtures' facilities & demand points, interleaved in one table
        facs = [
            Waypoint(*f.geometry().asPoint(), {"role": "facility", "name": f"fac {f['id']}"})
            for f in self.facilities.getFeatures()
        ]
        dems = [
            Waypoint(*f.geometry().asPoint(), {"role": "demand", "name": f"dem {f['id']}", "weight": 1.0})
            for f in self.demand.getFeatures()
        ]
        self.waypoints = dems[:5] + facs + dems[5:]

    def run_spopt(self, problem: SpoptProblem, **kwargs):
        facilities, demand = split_waypoints(self.waypoints)
        result = dict()

        def on_done(layers: List[QgsMapLayer], error: Optional[str]):
            result.update(layers=layers, error=error)
            loop.quit()

        run = SpoptRun(
            problem,
            facilities,
            demand,
            matrix_by_index(),
            **{
                "metric_idx": 0,
                "service_radius": 600,
                "n_facilities": 1,
                "draw_lines": False,
                "on_done": on_done,
                **kwargs,
            },
        )
        loop = QEventLoop()
        QTimer.singleShot(120_000, loop.quit)
        run.start()
        loop.exec()

        self.assertIn("layers", result, "timed out")
        return result["layers"], result["error"]

    def test_split_waypoints(self):
        facilities, demand = split_waypoints(self.waypoints)
        self.assertEqual([wp.attrs["name"] for wp in facilities], ["fac 1", "fac 2", "fac 3"])
        self.assertEqual(len(demand), 15)

    def test_points_layer(self):
        facilities, _ = split_waypoints(self.waypoints)
        facilities[1].attrs["predefined"] = True
        layer = points_layer("facilities", facilities, QgsField("predefined", QVariant.Int))
        self.assertEqual(
            [f.attributes() for f in layer.getFeatures()],
            [[0, "fac 1", 0], [1, "fac 2", 1], [2, "fac 3", 0]],
        )

    def test_lscp(self):
        (fac, dem), error = self.run_spopt(SpoptProblem.LSCP)
        self.assertIsNone(error)
        self.assertEqual((fac.name(), dem.name()), ("LSCP Facilities", "LSCP Demand"))
        # same as the Processing test on these fixtures
        self.assertEqual(fac.featureCount(), 2)
        self.assertEqual(dem.featureCount(), 18)
        # the table's attributes are carried over
        self.assertEqual(fac.fields().names(), ["id", "name", "predefined", FieldNames.DEMAND_COUNT])
        self.assertTrue(all(f["name"].startswith("fac ") for f in fac.getFeatures()))
        self.assertTrue(all(f["name"].startswith("dem ") for f in dem.getFeatures()))

    def test_mclp(self):
        (fac, dem), error = self.run_spopt(SpoptProblem.MCLP, n_facilities=1, draw_lines=True)
        self.assertIsNone(error)
        self.assertEqual(fac.featureCount(), 1)
        self.assertGreater(dem.featureCount(), 0)
        self.assertEqual(dem.geometryType(), QgsWkbTypes.GeometryType.LineGeometry)

    def test_error(self):
        layers, error = self.run_spopt(SpoptProblem.MCLP, n_facilities=5)
        self.assertEqual(layers, [])
        self.assertIn("Can't site 5 facilities", error)
