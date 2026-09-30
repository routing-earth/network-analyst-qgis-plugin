from typing import Any, Dict, List

from qgis.core import (
    QgsFeature,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
)

from .base_algorithm import SpoptBaseAlgorithm


class CoverageMixin:
    """The params shared by the coverage problems (LSCP, MCLP)."""

    IN_SERVICE_RADIUS = "INPUT_SERVICE_RADIUS"
    IN_PREDEFINED = "INPUT_PREDEFINED_FAC_FIELD"

    def init_coverage_params(self: SpoptBaseAlgorithm):
        self.addParameter(
            QgsProcessingParameterNumber(
                name=self.IN_SERVICE_RADIUS,
                description="Service radius, in the unit of the cost metric (seconds or meters)",
                type=QgsProcessingParameterNumber.Type.Double,
                minValue=0,
            )
        )
        # deliberately numeric: DataType.Boolean was introduced in 3.34 and shapefiles don't support it
        self.addParameter(
            QgsProcessingParameterField(
                name=self.IN_PREDEFINED,
                description="Facility field marking facilities which must be selected (1 = yes)",
                parentLayerParameterName=self.IN_FAC,
                type=QgsProcessingParameterField.DataType.Numeric,
                optional=True,
            )
        )

    def get_coverage_kwargs(
        self: SpoptBaseAlgorithm,
        parameters,
        context,
        fac_ids: List[Any],
        fac_feats: Dict[Any, QgsFeature],
    ) -> Dict[str, Any]:
        kwargs = {"service_radius": self.parameterAsDouble(parameters, self.IN_SERVICE_RADIUS, context)}

        if predefined_field := self.parameterAsString(parameters, self.IN_PREDEFINED, context):
            kwargs["predefined"] = [1 if fac_feats[i][predefined_field] == 1 else 0 for i in fac_ids]

        return kwargs
