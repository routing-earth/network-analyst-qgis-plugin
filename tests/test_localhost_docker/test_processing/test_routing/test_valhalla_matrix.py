from qgis.PyQt.QtCore import QVariant

from ....utilities import get_qgis_app
from ..processing_base import ProcessingBase

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.global_definitions import FieldNames
from valhalla.processing.routing.valhalla.matrix import ValhallaMatrixCar


class TestValhallaMatrix(ProcessingBase):
    def test_matrix_basic(self):
        params = {
            "INPUT_LAYER_1": self.layer_1,
            "INPUT_LAYER_2": self.layer_2,
            "INPUT_FIELD_1": "ID",
            "INPUT_FIELD_2": "ID_str",
        }
        alg = ValhallaMatrixCar()
        feats, _ = self.run_routing_algorithm(alg, params)
        self.assertEqual(len(feats), 9)
        self.assertEqual(feats[0].fields().field(FieldNames.SOURCE).type(), QVariant.Int)
        self.assertEqual(feats[0].fields().field(FieldNames.TARGET).type(), QVariant.String)

    def test_matrix_id_types_dont_leak(self):
        """The ID field types of one run must not stick to the shared default fields."""
        params = {
            "INPUT_LAYER_1": self.layer_1,
            "INPUT_LAYER_2": self.layer_2,
            "INPUT_FIELD_1": "ID_str",
            "INPUT_FIELD_2": "ID_str",
        }
        feats, _ = self.run_routing_algorithm(ValhallaMatrixCar(), params)
        self.assertEqual(feats[0].fields().field(FieldNames.SOURCE).type(), QVariant.String)

        # no ID fields: feature IDs, i.e. back to the default integer fields
        params = {"INPUT_LAYER_1": self.layer_1, "INPUT_LAYER_2": self.layer_2}
        feats, _ = self.run_routing_algorithm(ValhallaMatrixCar(), params)
        self.assertEqual(feats[0].fields().field(FieldNames.SOURCE).type(), QVariant.Int)
        self.assertEqual(feats[0].fields().field(FieldNames.TARGET).type(), QVariant.Int)
