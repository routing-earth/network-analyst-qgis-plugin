from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsLineString,
    QgsMultiLineString,
    QgsMultiPoint,
    QgsMultiPolygon,
    QgsPoint,
    QgsPolygon,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant

from ... import LocalhostDockerTestCase
from ...constants import WAYPOINTS_4326
from ...utilities import get_qgis_app

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.third_party.routingpy.routingpy.utils import decode_polyline6
from valhalla.utils.layer_utils import get_linear_cost_factors, get_wgs_coords_from_layer


class TestLayerUtils(LocalhostDockerTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # create one layer for each case the function handles:
        # [single point, single polygon, multi point, multi polygon]
        cls.single_point_layer = QgsVectorLayer("Point?crs=EPSG:4326", "single_point", "memory")
        for point in WAYPOINTS_4326:
            feat = QgsFeature()
            feat.setGeometry(QgsPoint(*point))
            cls.single_point_layer.dataProvider().addFeature(feat)

        cls.single_polygon_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "single_poly", "memory")
        for polygon in [WAYPOINTS_4326] * 2:  # create multiple polygons from same coordinates
            feat = QgsFeature()
            feat.setGeometry(QgsPolygon(QgsLineString([QgsPoint(*coord) for coord in polygon])))
            cls.single_polygon_layer.dataProvider().addFeature(feat)

        cls.multi_point_layer = QgsVectorLayer("MultiPoint?crs=EPSG:4326", "multi_point", "memory")
        feat = QgsFeature()
        multipoint = QgsMultiPoint()
        points = [QgsPoint(*coord) for coord in WAYPOINTS_4326]
        _ = [multipoint.addGeometry(point) for point in points]
        feat.setGeometry(multipoint)
        cls.multi_point_layer.dataProvider().addFeature(feat)

        cls.multi_polygon_layer = QgsVectorLayer("MultiPolygon?crs=EPSG:4326", "multi_poly", "memory")
        feat = QgsFeature()
        polys = [
            QgsPolygon(QgsLineString([QgsPoint(*coord) for coord in polygon]))
            for polygon in [WAYPOINTS_4326] * 2
        ]  # create multiple polygons from same coordinates
        multipoly = QgsMultiPolygon()
        _ = [multipoly.addGeometry(poly) for poly in polys]

        feat.setGeometry(multipoly)
        cls.multi_polygon_layer.dataProvider().addFeature(feat)

    def test_get_avoid_locations(self) -> None:
        for layer in (
            self.single_point_layer,
            self.single_polygon_layer,
            self.multi_point_layer,
            self.multi_polygon_layer,
        ):
            is_poly = layer.name().split("_")[1] == "poly"

            avoid_coords = get_wgs_coords_from_layer(layer)
            if is_poly:
                self.assertEqual(
                    avoid_coords,
                    [[*WAYPOINTS_4326, WAYPOINTS_4326[0]]]
                    * 2,  # First coordinate is repeated for Polygons
                    msg=f"get_avoid_locations failed with layer {layer.name()}",
                )
            else:  # points
                self.assertEqual(
                    avoid_coords,
                    WAYPOINTS_4326,
                    msg=f"get_avoid_locations failed with layer {layer.name()}",
                )

    def test_get_linear_cost_factors(self) -> None:
        line = [tuple(coord) for coord in WAYPOINTS_4326]

        def make_layer(wkb_type: str, epsg: int = 4326) -> QgsVectorLayer:
            layer = QgsVectorLayer(f"{wkb_type}?crs=EPSG:{epsg}", wkb_type, "memory")
            layer.dataProvider().addAttributes([QgsField("factor", QVariant.Double)])
            layer.updateFields()
            return layer

        def add_feature(layer: QgsVectorLayer, geom, factor) -> None:
            feat = QgsFeature(layer.fields())
            if geom:
                feat.setGeometry(geom)
            feat["factor"] = factor
            layer.dataProvider().addFeature(feat)

        def make_line(coords) -> QgsLineString:
            return QgsLineString([QgsPoint(*coord) for coord in coords])

        # features without a factor or without a geometry are skipped
        single_layer = make_layer("LineString")
        add_feature(single_layer, make_line(line), 2.5)
        add_feature(single_layer, make_line(reversed(line)), 10)
        add_feature(single_layer, make_line(line), None)
        add_feature(single_layer, None, 3)

        factors = get_linear_cost_factors(single_layer, "factor")
        self.assertEqual([f["factor"] for f in factors], [2.5, 10.0])
        self.assertEqual(decode_polyline6(factors[0]["shape"]), line)
        self.assertEqual(decode_polyline6(factors[1]["shape"]), list(reversed(line)))

        # each part of a multi line gets the feature's factor
        multi_layer = make_layer("MultiLineString")
        multi_line = QgsMultiLineString()
        multi_line.addGeometry(make_line(line[:2]))
        multi_line.addGeometry(make_line(line[1:]))
        add_feature(multi_layer, multi_line, 4)

        factors = get_linear_cost_factors(multi_layer, "factor")
        self.assertEqual([f["factor"] for f in factors], [4.0, 4.0])
        self.assertEqual(decode_polyline6(factors[0]["shape"]), line[:2])
        self.assertEqual(decode_polyline6(factors[1]["shape"]), line[1:])

        # other CRS are transformed to WGS84
        projected_layer = make_layer("LineString", 3857)
        geom = QgsGeometry(make_line(line))
        geom.transform(
            QgsCoordinateTransform(
                QgsCoordinateReferenceSystem.fromEpsgId(4326),
                QgsCoordinateReferenceSystem.fromEpsgId(3857),
                QgsProject.instance(),
            )
        )
        add_feature(projected_layer, geom, 2)

        factors = get_linear_cost_factors(projected_layer, "factor")
        for decoded, expected in zip(decode_polyline6(factors[0]["shape"]), line):
            self.assertAlmostEqual(decoded[0], expected[0], 5)
            self.assertAlmostEqual(decoded[1], expected[1], 5)

        self.assertEqual(get_linear_cost_factors(make_layer("LineString"), "factor"), [])
