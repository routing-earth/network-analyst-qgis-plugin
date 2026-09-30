import json
import unittest
from typing import List, Tuple

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingOutputLayerDefinition,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant
from tests.utilities import get_qgis_app

from ... import TEST_DIR

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.core import pypi  # noqa: E402
from valhalla.global_definitions import FieldNames  # noqa: E402
from valhalla.processing.spatial_optimization.lscp import LSCPAlgorithm  # noqa: E402

DATA_DIR = TEST_DIR / "data"
# the matrix in tests/data: facilities 1-3 (source) x demand points 1-15 (target), IDs = "id" field
RADIUS = 600

_FIELD_TYPES = {int: QVariant.Int, float: QVariant.Double, str: QVariant.String}


def load_geojson(name: str) -> QgsVectorLayer:
    """
    A memory layer from tests/data/<name>.geojson (points or no geometry). Not via OGR: with
    the get_qgis_app() harness, reading any OGR layer crashes the process at exit (std::bad_alloc
    after the atexit exitQgis(), seen with QGIS 4.2.1 / GDAL 3.13.3).
    """
    data = json.loads(DATA_DIR.joinpath(f"{name}.geojson").read_text())
    # the files carry OGC URNs, which the memory provider's URI doesn't parse
    urn = data.get("crs", {}).get("properties", {}).get("name", "urn:ogc:def:crs:OGC:1.3:CRS84")
    crs = QgsCoordinateReferenceSystem.fromOgcWmsCrs(urn).authid()
    has_geom = data["features"][0]["geometry"] is not None
    layer = QgsVectorLayer(f"{'Point' if has_geom else 'None'}?crs={crs}", name, "memory")

    props = data["features"][0]["properties"]
    layer.dataProvider().addAttributes([QgsField(k, _FIELD_TYPES[type(v)]) for k, v in props.items()])
    layer.updateFields()

    feats = []
    for gj_feat in data["features"]:
        feat = QgsFeature(layer.fields())
        feat.setAttributes(list(gj_feat["properties"].values()))
        if has_geom:
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*gj_feat["geometry"]["coordinates"])))
        feats.append(feat)
    layer.dataProvider().addFeatures(feats)

    return layer


class TestLSCP(unittest.TestCase):
    """Needs no valhalla: runs on the static matrix in tests/data. Installs spopt if missing."""

    @classmethod
    def setUpClass(cls):
        if not pypi.is_installed(pypi.SPOPT_PKG):
            pypi.install(pypi.SPOPT_PKG, pypi.PyPiState.NOT_INSTALLED)

        cls.matrix = load_geojson("matrix")
        cls.facilities = load_geojson("facilities")
        cls.demand = load_geojson("demand_points")
        cls.demand_utm = load_geojson("demand_points_utm")

    def run_alg(self, params: dict) -> Tuple[QgsVectorLayer, QgsVectorLayer]:
        alg = LSCPAlgorithm()
        alg.initAlgorithm({})
        ctx = QgsProcessingContext()
        feedback = QgsProcessingFeedback()
        params = {
            alg.IN_MATRIX: self.matrix,
            alg.IN_FAC: self.facilities,
            alg.IN_FAC_ID: "id",
            alg.IN_DEM: self.demand,
            alg.IN_DEM_ID: "id",
            alg.IN_SERVICE_RADIUS: RADIUS,
            alg.OUT_FAC: QgsProcessingOutputLayerDefinition("TEMPORARY_OUTPUT"),
            alg.OUT_DEM: QgsProcessingOutputLayerDefinition("TEMPORARY_OUTPUT"),
            **params,
        }
        self.assertTrue(alg.prepareAlgorithm(params, ctx, feedback))
        out = alg.processAlgorithm(params, ctx, feedback)

        # the context owns the temporary layers and deletes them with itself
        return ctx.takeResultLayer(out[alg.OUT_FAC]), ctx.takeResultLayer(out[alg.OUT_DEM])

    @staticmethod
    def feats(layer: QgsVectorLayer) -> List[QgsFeature]:
        return list(layer.getFeatures())

    def test_lscp(self):
        fac, dem = self.run_alg({})
        self.assertEqual(len(self.feats(fac)), 2)
        self.assertEqual(len(self.feats(dem)), 18)  # coverage: some are covered twice
        self.assertEqual(QgsWkbTypes.flatType(fac.wkbType()), QgsWkbTypes.Type.Point)
        self.assertEqual(QgsWkbTypes.flatType(dem.wkbType()), QgsWkbTypes.Type.Point)

        # every demand point is covered, within the radius, by a selected facility
        selected = {f[FieldNames.ID] for f in self.feats(fac)}
        self.assertEqual({f[FieldNames.ID] for f in self.feats(dem)}, set(range(1, 16)))
        for f in self.feats(dem):
            self.assertIn(f[FieldNames.FACILITY_ID], selected)
            self.assertLessEqual(f[FieldNames.DURATION], RADIUS)
        self.assertEqual(sum(f[FieldNames.DEMAND_COUNT] for f in self.feats(fac)), 18)

        # the geometries are the joined ones
        fac_geoms = {f["id"]: f.geometry().asWkt() for f in self.facilities.getFeatures()}
        for f in self.feats(fac):
            self.assertEqual(f.geometry().asWkt(), fac_geoms[f[FieldNames.ID]])
        demand_geoms = {f["id"]: f.geometry().asWkt() for f in self.demand.getFeatures()}
        for f in self.feats(dem):
            self.assertEqual(f.geometry().asWkt(), demand_geoms[f[FieldNames.ID]])

    def test_feature_id_fallback(self):
        """Without ID fields the matrix IDs are feature IDs, which here equal the "id" field."""
        self.assertEqual([f.id() for f in self.facilities.getFeatures()], [1, 2, 3])
        fac, dem = self.run_alg({"INPUT_FAC_ID": "", "INPUT_DEM_ID": ""})
        self.assertEqual(len(self.feats(fac)), 2)
        self.assertEqual(len(self.feats(dem)), 18)

    def test_predefined(self):
        fac, dem = self.run_alg({"INPUT_PREDEFINED_FAC_FIELD": "predefined"})
        self.assertEqual(len(self.feats(fac)), 3)
        self.assertEqual(len(self.feats(dem)), 26)
        self.assertIn(3, {f[FieldNames.ID] for f in self.feats(fac)})

    def test_lines_across_crs(self):
        """Facilities in WGS84, demand points in UTM: lines end exactly on both."""
        fac, dem = self.run_alg({"INPUT_DEM_POINT_LAYER": self.demand_utm, "INPUT_LINES": True})
        self.assertEqual(QgsWkbTypes.flatType(dem.wkbType()), QgsWkbTypes.Type.LineString)
        self.assertEqual(dem.crs(), self.demand_utm.crs())
        demand_pts = {f["id"]: f.geometry().asPoint() for f in self.demand_utm.getFeatures()}
        for f in self.feats(dem):
            line = f.geometry().asPolyline()
            self.assertEqual(len(line), 2)
            self.assertEqual(line[0], demand_pts[f[FieldNames.ID]])
            # the facility end was transformed to UTM, i.e. it's far off from lng/lat
            self.assertGreater(abs(line[1].x()), 1000)

    def test_missing_layer(self):
        """Both layers are required."""
        for layer_param in ("INPUT_FAC_LAYER", "INPUT_DEM_POINT_LAYER"):
            with self.assertRaises(QgsProcessingException):
                self.run_alg({layer_param: None})

    def test_wrong_layer(self):
        """A layer which doesn't have all the matrix IDs can't be the one the matrix was built with."""
        with self.assertRaisesRegex(QgsProcessingException, "no features for the matrix IDs 4, 5"):
            self.run_alg({"INPUT_DEM_POINT_LAYER": self.facilities})

    def test_wrong_id_field(self):
        """Joining on a non-unique field the matrix wasn't built with: all weights are 1."""
        with self.assertRaisesRegex(QgsProcessingException, "several features for the matrix IDs 1"):
            self.run_alg({"INPUT_DEM_ID": "weights"})

    def test_duplicate_matrix_rows(self):
        """A (source, target) pair may only appear once, else one cost would silently win."""
        matrix = self.matrix.materialize(QgsFeatureRequest())
        matrix.dataProvider().addFeatures([next(self.matrix.getFeatures())])
        with self.assertRaisesRegex(QgsProcessingException, "more than one row"):
            self.run_alg({"INPUT_MATRIX_LAYER": matrix})

    def test_duplicate_layer_ids(self):
        """The ID field must identify the matrix' features unambiguously."""
        facilities = self.facilities.materialize(QgsFeatureRequest())
        facilities.dataProvider().addFeatures([next(self.facilities.getFeatures())])
        with self.assertRaisesRegex(QgsProcessingException, "several features"):
            self.run_alg({"INPUT_FAC_LAYER": facilities})

    def test_infeasible(self):
        with self.assertRaisesRegex(QgsProcessingException, "Infeasible"):
            self.run_alg({"INPUT_SERVICE_RADIUS": 100})
