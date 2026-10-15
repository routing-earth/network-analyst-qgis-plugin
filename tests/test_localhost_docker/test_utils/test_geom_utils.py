import unittest

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY

from valhalla.third_party.routingpy.routingpy.utils import decode_polyline6
from valhalla.utils.geom_utils import encode_polyline6, point_to_wgs84

from ...constants import WAYPOINTS_3857, WAYPOINTS_4326


class TestGeomUtils(unittest.TestCase):
    def test_point_to_wgs84_forward(self):
        crs = QgsCoordinateReferenceSystem.fromEpsgId(3857)
        exp = WAYPOINTS_4326
        for idx, pt in enumerate(WAYPOINTS_3857):
            proj_pt = point_to_wgs84(QgsPointXY(*pt), crs)
            self.assertAlmostEqual(proj_pt.x(), exp[idx][0], 5)
            self.assertAlmostEqual(proj_pt.y(), exp[idx][1], 5)

    def test_point_to_wgs84_backward(self):
        crs = QgsCoordinateReferenceSystem.fromEpsgId(3857)
        exp = WAYPOINTS_3857
        for idx, pt in enumerate(WAYPOINTS_4326):
            proj_pt = point_to_wgs84(
                QgsPointXY(*pt), crs, QgsCoordinateTransform.TransformDirection.ReverseTransform
            )
            self.assertEqual(round(proj_pt.x(), 1), exp[idx][0])
            self.assertEqual(round(proj_pt.y(), 1), exp[idx][1])

    def test_encode_polyline6(self):
        # negative deltas, negative coordinates and a z dimension which has to be ignored
        coords = [[1.539283, 42.612798, 5.0], [1.543225, 42.523528], [-1.709114, -42.542892]]
        encoded = encode_polyline6(coords)

        self.assertEqual(decode_polyline6(encoded), [tuple(coord[:2]) for coord in coords])
        self.assertEqual(encode_polyline6([]), "")
