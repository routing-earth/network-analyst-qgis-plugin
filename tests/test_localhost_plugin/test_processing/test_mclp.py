from qgis.core import QgsProcessingException

from valhalla.global_definitions import FieldNames
from valhalla.processing.spatial_optimization.mclp import MCLPAlgorithm

from .spopt_base import SpoptProcessingBase

RADIUS = 500


class TestMCLP(SpoptProcessingBase):
    ALG = MCLPAlgorithm

    def run_alg(self, params: dict):
        return super().run_alg({"INPUT_SERVICE_RADIUS": RADIUS, "INPUT_N_FAC": 1, **params})

    def test_mclp(self):
        fac, dem = self.run_alg({})
        self.assertEqual(len(self.feats(fac)), 1)
        self.assertEqual(len(self.feats(dem)), 12)

        # not every demand point is covered, the ones which are lie within the radius
        (selected,) = self.feats(fac)
        self.assertEqual(selected[FieldNames.DEMAND_COUNT], 12)
        for f in self.feats(dem):
            self.assertEqual(f[FieldNames.FACILITY_ID], selected[FieldNames.ID])
            self.assertLessEqual(f[FieldNames.DURATION], RADIUS)

    def test_weights(self):
        """Demand point 12 weighs 15: covering it beats covering 12 light ones."""
        unweighted, _ = self.run_alg({})
        fac, dem = self.run_alg({"INPUT_DEM_WEIGHTS": "weights"})
        self.assertEqual(len(self.feats(fac)), 1)
        self.assertEqual(len(self.feats(dem)), 4)
        self.assertIn(12, {f[FieldNames.ID] for f in self.feats(dem)})
        self.assertNotEqual(self.feats(fac)[0][FieldNames.ID], self.feats(unweighted)[0][FieldNames.ID])

    def test_n_facilities(self):
        fac, dem = self.run_alg({"INPUT_N_FAC": 3})
        self.assertEqual(len(self.feats(fac)), 3)
        self.assertEqual(len(self.feats(dem)), 21)

    def test_too_many_facilities(self):
        with self.assertRaisesRegex(QgsProcessingException, "only has 3"):
            self.run_alg({"INPUT_N_FAC": 4})

    def test_predefined(self):
        """Facility 3 is predefined, so it's the one, whatever it covers."""
        fac, _ = self.run_alg({"INPUT_PREDEFINED_FAC_FIELD": "predefined"})
        self.assertEqual([f[FieldNames.ID] for f in self.feats(fac)], [3])

    def test_selected_without_demand(self):
        """With a tiny radius nobody is covered, the facilities are still sited."""
        fac, dem = self.run_alg({"INPUT_SERVICE_RADIUS": 1, "INPUT_N_FAC": 2})
        self.assertEqual(len(self.feats(fac)), 2)
        self.assertEqual([f[FieldNames.DEMAND_COUNT] for f in self.feats(fac)], [0, 0])
        self.assertEqual(len(self.feats(dem)), 0)
