import json
import unittest
from typing import List, Tuple, Type

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingOutputLayerDefinition,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant
from tests.utilities import get_qgis_app

from ... import TEST_DIR

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.core import pypi  # noqa: E402
from valhalla.processing.spatial_optimization.base_algorithm import SpoptBaseAlgorithm  # noqa: E402

DATA_DIR = TEST_DIR / "data"
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


class SpoptProcessingBase(unittest.TestCase):
    """
    Needs no valhalla: runs on the static matrix in tests/data, which has facilities 1-3
    (source) x demand points 1-15 (target) by their "id" field. Installs spopt if missing.
    """

    ALG: Type[SpoptBaseAlgorithm]

    @classmethod
    def setUpClass(cls):
        if not pypi.is_installed(pypi.SPOPT_PKG):
            pypi.install(pypi.SPOPT_PKG, pypi.PyPiState.NOT_INSTALLED)

        cls.matrix = load_geojson("matrix")
        cls.facilities = load_geojson("facilities")
        cls.demand = load_geojson("demand_points")
        cls.demand_utm = load_geojson("demand_points_utm")

    def run_alg(self, params: dict) -> Tuple[QgsVectorLayer, QgsVectorLayer]:
        """Runs ALG on the test layers, params override/extend the defaults."""
        alg = self.ALG()
        alg.initAlgorithm({})
        ctx = QgsProcessingContext()
        feedback = QgsProcessingFeedback()
        params = {
            alg.IN_MATRIX: self.matrix,
            alg.IN_FAC: self.facilities,
            alg.IN_FAC_ID: "id",
            alg.IN_DEM: self.demand,
            alg.IN_DEM_ID: "id",
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
