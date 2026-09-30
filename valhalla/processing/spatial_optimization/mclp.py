from qgis.core import (
    QgsProcessingException,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
)

from ...core.spopt import SpoptProblem
from .base_algorithm import SpoptBaseAlgorithm
from .coverage_mixin import CoverageMixin


class MCLPAlgorithm(CoverageMixin, SpoptBaseAlgorithm):
    PROBLEM = SpoptProblem.MCLP

    IN_N_FAC = "INPUT_N_FAC"
    IN_WEIGHTS = "INPUT_DEM_WEIGHTS"

    def displayName(self):
        return "Maximal Coverage Location Problem (MCLP)"

    def init_problem_params(self):
        self.addParameter(
            QgsProcessingParameterNumber(
                name=self.IN_N_FAC,
                description="Number of facilities to site",
                type=QgsProcessingParameterNumber.Type.Integer,
                minValue=1,
                defaultValue=1,
            )
        )
        self.init_coverage_params()
        self.addParameter(
            QgsProcessingParameterField(
                name=self.IN_WEIGHTS,
                description="Demand point weight field (all weigh the same if empty)",
                parentLayerParameterName=self.IN_DEM,
                type=QgsProcessingParameterField.DataType.Numeric,
                optional=True,
            )
        )

    def get_problem_kwargs(self, parameters, context, fac_ids, dem_ids, fac_feats, dem_feats):
        kwargs = self.get_coverage_kwargs(parameters, context, fac_ids, fac_feats)

        n_fac = self.parameterAsInt(parameters, self.IN_N_FAC, context)
        if n_fac > len(fac_ids):
            raise QgsProcessingException(
                f"Can't site {n_fac} facilities, the cost matrix only has {len(fac_ids)}"
            )
        kwargs["p_facilities"] = n_fac

        weights = [1.0] * len(dem_ids)
        if weights_field := self.parameterAsString(parameters, self.IN_WEIGHTS, context):
            try:
                weights = [float(dem_feats[i][weights_field]) for i in dem_ids]
            except (TypeError, ValueError):
                raise QgsProcessingException(f"The weight field {weights_field} has empty values")
            if min(weights) < 0:
                raise QgsProcessingException(f"The weight field {weights_field} has negative values")
        kwargs["weights"] = weights

        return kwargs
