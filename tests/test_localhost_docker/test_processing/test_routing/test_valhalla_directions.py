from qgis.core import (
    QgsFeature,
    QgsField,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant

from ....utilities import get_qgis_app
from ...test_processing.processing_base import ProcessingBase

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.global_definitions import (
    FieldNames,
    RouterMethod,
    RoutingMetric,
)
from valhalla.processing.routing.valhalla.directions import ValhallaDirectionsPedestrian


class TestValhallaDirections(ProcessingBase):
    def test_single_point(self):
        params = {"INPUT_LAYER_1": self.layer_1}
        alg = ValhallaDirectionsPedestrian()
        feats, _ = self.run_routing_algorithm(alg, params)

        self.assertEqual(len(feats), 1)

    def test_single_point_shortest(self):
        params_shortest = {
            "INPUT_LAYER_1": self.layer_1,
            "INPUT_MODE": int(RoutingMetric.SHORTEST),
        }
        params_fastest = {
            "INPUT_LAYER_1": self.layer_1,
            "INPUT_MODE": int(RoutingMetric.FASTEST),
        }

        alg = ValhallaDirectionsPedestrian()
        feats_shortest, _ = self.run_routing_algorithm(alg, params_shortest)
        feats_fastest, _ = self.run_routing_algorithm(alg, params_fastest)

        self.assertEqual(len(feats_shortest), 1)
        self.assertEqual(len(feats_fastest), 1)

        self.assertLess(
            feats_shortest[0][FieldNames.DURATION],
            feats_fastest[0][FieldNames.DURATION],
        )

    def test_avoid_polygon(self):
        params = {
            "INPUT_LAYER_1": self.layer_1,
            "INPUT_AVOID_POLYGONS": self.avoid_polygon_layer,
        }
        alg = ValhallaDirectionsPedestrian()
        feats, _ = self.run_routing_algorithm(alg, params)

        # TODO: this doesn't actually test anything
        self.assertEqual(len(feats), 1)

    def test_linear_factors(self):
        # two routes with two waypoints each: a route with more waypoints isn't a valid input, as
        # Valhalla can't edge walk its merged legs
        alg = ValhallaDirectionsPedestrian()
        feats, _ = self.run_routing_algorithm(alg, {"INPUT_LAYER_1": self.layer_mp})

        # the routes are map matched by definition, so penalize them and route again
        factor_layer = QgsVectorLayer("LineString?crs=EPSG:4326", "layer_linear_factors", "memory")
        factor_layer.dataProvider().addAttributes([QgsField("factor", QVariant.Double)])
        factor_layer.updateFields()
        for route in feats:
            feat = QgsFeature(factor_layer.fields())
            feat.setGeometry(route.geometry())
            feat["factor"] = 100.0
            factor_layer.dataProvider().addFeature(feat)

        params = {
            "INPUT_LAYER_1": self.layer_mp,
            "INPUT_LINEAR_FACTORS": factor_layer,
            "INPUT_LINEAR_FACTORS_FIELD": "factor",
        }
        feats_factored, _ = self.run_routing_algorithm(alg, params)

        self.assertEqual(len(feats_factored), 2)
        for route, route_factored in zip(feats, feats_factored):
            self.assertGreater(route_factored[FieldNames.DISTANCE], route[FieldNames.DISTANCE])

        # the factor field is mandatory once there's a layer
        del params["INPUT_LINEAR_FACTORS_FIELD"]
        with self.assertRaises(QgsProcessingException):
            self.run_routing_algorithm(alg, params)

    def test_multi_point(self):
        params = {"INPUT_LAYER_1": self.layer_mp}
        alg = ValhallaDirectionsPedestrian()
        feats, progress_changed_vals = self.run_routing_algorithm(alg, params)

        self.assertEqual(len(feats), 2)
        self.assertEqual(progress_changed_vals, [50, 100])

    def test_two_layers_row_by_row(self):
        params = {
            "INPUT_LAYER_1": self.layer_1,
            "INPUT_LAYER_2": self.layer_2,
            "INPUT_FIELD_1": "ID",
            "INPUT_FIELD_2": "ID",
            "INPUT_MERGE_STRATEGY": 0,
        }
        alg = ValhallaDirectionsPedestrian()
        feats, progress_changed_vals = self.run_routing_algorithm(alg, params)

        self.assertEqual(len(feats), 3)
        self.assertEqual(progress_changed_vals, [33, 66, 100])

    def test_two_layers_all_by_all(self):
        params = {
            "INPUT_LAYER_1": self.layer_1,
            "INPUT_LAYER_2": self.layer_2,
            "INPUT_MERGE_STRATEGY": 1,
        }
        alg = ValhallaDirectionsPedestrian()
        feats, progress_changed_vals = self.run_routing_algorithm(alg, params)

        self.assertEqual(len(feats), 9)
        self.assertEqual(progress_changed_vals, [int((x + 1) / 9 * 100) for x in range(9)])

    def test_two_layers_fail(self):
        """Tests whether specifying only one field with the row-by-row strategy raises an exception."""
        params = {
            "INPUT_LAYER_1": self.layer_1,
            "INPUT_LAYER_2": self.layer_2,
            "INPUT_FIELD_1": "ID",
            "INPUT_MERGE_STRATEGY": 0,
        }

        alg = ValhallaDirectionsPedestrian()
        alg.initAlgorithm({})
        ctx = QgsProcessingContext()
        feedback = QgsProcessingFeedback()
        alg.prepareAlgorithm(params, ctx, feedback)
        with self.assertRaises(QgsProcessingException):
            alg.processAlgorithm(params, ctx, feedback)

    def test_single_point_bindings(self):
        """Valhalla directions processing algorithm returns one route given a single point layer using bindings."""
        params = {
            "INPUT_METHOD": RouterMethod.LOCAL,
            "INPUT_PACKAGE": "andorra_tiles.tar",
            "INPUT_LAYER_1": self.layer_1,
        }
        alg = ValhallaDirectionsPedestrian()
        feats, _ = self.run_routing_algorithm(alg, params)

        self.assertEqual(len(feats), 1)
