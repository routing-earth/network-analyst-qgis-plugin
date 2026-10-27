from typing import Optional, Union

from qgis.core import (
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
)

from ...global_definitions import (
    DEFAULT_LAYER_FIELDS,
    FieldNames,
    RouterEndpoint,
    RouterProfile,
    RouterType,
)
from ...third_party.routingpy import routingpy
from ...utils.layer_utils import get_wgs_coords_from_layer
from ...utils.misc_utils import wrap_in_html_tag
from ..routing.base_algorithm import (
    ValhallaBaseAlgorithm,
)


class MatrixBase(ValhallaBaseAlgorithm):
    IN_2 = "INPUT_LAYER_2"
    IN_FIELD_2 = "INPUT_FIELD_2"

    def __init__(
        self,
        provider: Union[RouterType, str],
        profile: Optional[Union[RouterProfile, str]] = None,
    ):
        super(MatrixBase, self).__init__(
            provider=provider,
            endpoint=RouterEndpoint.MATRIX,
            profile=profile,
        )

    def group(self):
        return "Matrix"

    def groupId(self):
        return "valhalla_matrix"

    def initAlgorithm(self, configuration, p_str=None, Any=None, *args, **kwargs):
        self.init_base_params(multi_layer=True)
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                name=self.IN_2,
                description=f"{wrap_in_html_tag('Input point layer 2', 'b')}. "
                "If provided, directions between points in each layer will be computed",
                types=[QgsProcessing.SourceType.TypeVectorPoint],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                name=self.IN_FIELD_2,
                description="Layer 2 ID Field (can be used for joining)",
                parentLayerParameterName=self.IN_2,
                optional=True,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        (
            layer_1,
            layer_field_name_1,
            params,
            results_factory,
        ) = self.get_base_params(parameters, context)
        layer_2 = self.parameterAsSource(parameters, self.IN_2, context)
        layer_field_name_2 = self.parameterAsString(parameters, self.IN_FIELD_2, context)

        # copies: the ID types are set per run and the defaults are shared, also with the dock
        field_list = [QgsField(f) for f in DEFAULT_LAYER_FIELDS[self.endpoint]]
        if layer_field_name_1:
            layer_1_id_type = layer_1.fields().field(layer_field_name_1).type()
            field_list[2].setType(layer_1_id_type)

        if layer_field_name_2:
            layer_2_id_type = layer_2.fields().field(layer_field_name_2).type()
            field_list[3].setType(layer_2_id_type)

        return_fields = QgsFields()

        for f in field_list:
            return_fields.append(f)

        sink, dest_id = self.get_feature_sink(parameters, context, return_fields)
        from_coords = get_wgs_coords_from_layer(layer_1)
        from_indices = len(from_coords)

        to_coords = get_wgs_coords_from_layer(layer_2)
        to_indices = from_indices + len(to_coords)

        coords = from_coords + to_coords

        params["sources"] = list(range(from_indices))
        params["destinations"] = list(range(from_indices, to_indices))

        try:
            feature_iter = results_factory.get_results(self.endpoint, coords, params, return_fields)
        except (
            routingpy.exceptions.RouterApiError,
            routingpy.exceptions.RouterServerError,
        ) as e:
            raise QgsProcessingException(f"HTTP {e.status}: {e.message}")

        for feature_1 in layer_1.getFeatures():
            for feature_2 in layer_2.getFeatures():
                result_feat = next(feature_iter)

                if layer_field_name_1:
                    result_feat[FieldNames.SOURCE] = feature_1[layer_field_name_1]
                else:
                    i = int(feature_1.id())
                    result_feat.setAttribute(
                        2, i
                    )  # for some reason the dict-like attr setting does not work here

                if layer_field_name_2:
                    result_feat[FieldNames.TARGET] = feature_2[layer_field_name_2]
                else:
                    i = int(feature_2.id())
                    result_feat.setAttribute(3, i)

                sink.addFeature(result_feat)

        return {self.OUT: dest_id}
