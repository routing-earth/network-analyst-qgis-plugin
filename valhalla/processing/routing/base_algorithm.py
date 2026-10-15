from pathlib import Path
from typing import List, Optional, Tuple, Type

from qgis.core import (
    QgsFeatureSink,
    QgsFeatureSource,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon

from ...core.results_factory import ResultsFactory
from ...core.settings import DEFAULT_PROVIDERS, ProviderSetting, ValhallaSettings
from ...global_definitions import (
    SETTINGS_WIDGETS_MAP,
    RouterEndpoint,
    RouterMethod,
    RouterProfile,
    RouterType,
    RoutingMetric,
)
from ...gui.widgets.costing_settings.widget_settings_valhalla_base import (
    ValhallaSettingsBase,
)
from ...utils.geom_utils import WGS84
from ...utils.layer_utils import get_linear_cost_factors, get_wgs_coords_from_layer
from ...utils.misc_utils import wrap_in_html_tag
from ...utils.resource_utils import get_icon
from ..processing_definitions import HELP_DIR


class ValhallaBaseAlgorithm(QgsProcessingAlgorithm):

    IN_PROVIDER = "INPUT_PROVIDER"
    # IN_URL = "INPUT_URL"
    IN_PKG = "INPUT_PACKAGE"
    IN_MODE = "INPUT_MODE"
    IN_AVOID_LOCATIONS = "INPUT_AVOID_LOCATIONS"
    IN_AVOID_POLYGONS = "INPUT_AVOID_POLYGONS"
    IN_LINEAR_FACTORS = "INPUT_LINEAR_FACTORS"
    IN_LINEAR_FACTORS_FIELD = "INPUT_LINEAR_FACTORS_FIELD"
    IN_1 = "INPUT_LAYER_1"
    IN_FIELD_1 = "INPUT_FIELD_1"

    OUT = "OUTPUT"
    WITH_COSTING_OPTIONS = True
    IN_1_TYPES = [QgsProcessing.SourceType.TypeVectorPoint]

    def __init__(
        self,
        provider: RouterType,
        endpoint: RouterEndpoint,
        profile: Optional[RouterProfile] = None,
    ):
        super(ValhallaBaseAlgorithm, self).__init__()

        self.router = provider  # avoid overriding QGIS' built-in provider method
        self.providers: List[ProviderSetting] = list()  # will be set in init_base_params
        self.endpoint = endpoint
        self.profile = profile
        if provider == RouterType.VALHALLA and endpoint != RouterEndpoint.ELEVATION:
            costing_widget: Type[ValhallaSettingsBase] = SETTINGS_WIDGETS_MAP[self.profile]["widget"]

            # we keep the costing params dict for retrieving the parameter values later
            self.costing_defaults = self.get_costing_defaults(costing_widget)

    def tr(self, string):
        """
        Returns a translatable string with the self.tr() function.
        """
        return QCoreApplication.translate("Processing", string)

    def init_base_params(self, multi_layer: bool = False) -> None:
        """
        We call this method in each subclass' initAlgorithm method, since these parameters are constant
        across algorithms.

        :param multi_layer: If True, adds a number to the input layer and input field descriptions.
        """

        # make sure we have at least one remote HTTP API URL
        settings = ValhallaSettings()
        if not settings.get_providers(RouterType.VALHALLA):
            for prov in DEFAULT_PROVIDERS:
                settings.set_provider(RouterType.VALHALLA.lower(), prov)

        self.providers = settings.get_providers(RouterType.VALHALLA)
        servers = [prov.name for prov in self.providers]

        url_param = QgsProcessingParameterEnum(
            self.IN_PROVIDER,
            "Provider",
            servers,
            defaultValue=servers[0],
        )
        self.addParameter(url_param)

        # pkg_param = QgsProcessingParameterEnum(
        #     self.IN_PKG,
        #     "Package path (used if method is Bindings)",
        #     options=self.pkgs,
        #     defaultValue=0,
        # )
        # self.addParameter(pkg_param)

        # url_param = QgsProcessingParameterString(
        #     self.IN_URL, "URL (used if method is HTTP)", defaultValue=defaultUrl
        # )
        # self.addParameter(url_param)

        if self.router == RouterType.VALHALLA and self.WITH_COSTING_OPTIONS:
            mode_param = QgsProcessingParameterEnum(
                self.IN_MODE,
                "Mode",
                options=list(RoutingMetric),
                defaultValue=RoutingMetric[0],
            )
            mode_param.setHelp(
                "If 'shortest', Valhalla will solely use distance as cost and disregard all other costs, penalties and factors."
            )

            mode_param.setFlags(QgsProcessingParameterDefinition.Flag.FlagAdvanced)

            self.addParameter(mode_param)

            avoid_locations_param = QgsProcessingParameterFeatureSource(
                name=self.IN_AVOID_LOCATIONS,
                description="Point layer with locations to avoid",
                types=[QgsProcessing.SourceType.TypeVectorPoint],
                optional=True,
            )

            avoid_locations_param.setFlags(
                avoid_locations_param.flags() | QgsProcessingParameterDefinition.Flag.FlagAdvanced
            )
            self.addParameter(avoid_locations_param)

            avoid_polygons_param = QgsProcessingParameterFeatureSource(
                name=self.IN_AVOID_POLYGONS,
                description="Point layer with polygons to avoid",
                types=[QgsProcessing.SourceType.TypeVectorPolygon],
                optional=True,
            )
            avoid_polygons_param.setFlags(
                avoid_polygons_param.flags() | QgsProcessingParameterDefinition.Flag.FlagAdvanced
            )
            self.addParameter(avoid_polygons_param)

            linear_factors_param = QgsProcessingParameterFeatureSource(
                name=self.IN_LINEAR_FACTORS,
                description="Line layer with map matched lines to factor the costs along",
                types=[QgsProcessing.SourceType.TypeVectorLine],
                optional=True,
            )
            linear_factors_param.setHelp(
                "Sent as 'linear_cost_factors'. The lines must already be map matched to the routing graph, "
                "i.e. follow its edges without a break, e.g. a route between two waypoints."
            )
            linear_factors_param.setFlags(
                linear_factors_param.flags() | QgsProcessingParameterDefinition.Flag.FlagAdvanced
            )
            self.addParameter(linear_factors_param)

            linear_factors_field_param = QgsProcessingParameterField(
                name=self.IN_LINEAR_FACTORS_FIELD,
                description="Factor field of the map matched lines",
                parentLayerParameterName=self.IN_LINEAR_FACTORS,
                type=QgsProcessingParameterField.DataType.Numeric,
                optional=True,
            )
            linear_factors_field_param.setFlags(
                linear_factors_field_param.flags() | QgsProcessingParameterDefinition.Flag.FlagAdvanced
            )
            self.addParameter(linear_factors_field_param)
            self.setup_costing_options()

        input_layer_desc = f"Input point layer{' 1' if multi_layer else ''}"
        input_param = QgsProcessingParameterFeatureSource(
            name=self.IN_1,
            description=wrap_in_html_tag(input_layer_desc, "b"),
            types=self.IN_1_TYPES,
        )
        self.addParameter(input_param)

        input_field_desc = f"Layer{' 1' if multi_layer else ''} ID field (can be used for joining)"
        input_field_param = QgsProcessingParameterField(
            name=self.IN_FIELD_1,
            description=input_field_desc,
            parentLayerParameterName=self.IN_1,
            optional=True,
        )
        self.addParameter(input_field_param)

        output_param = QgsProcessingParameterFeatureSink(
            name=self.OUT,
            description=f"{self.router.lower()}_{self.endpoint.lower()}"
            f"{f'_{self.profile}' if self.router == RouterType.VALHALLA and self.profile else str()}",
            createByDefault=True,
        )
        self.addParameter(output_param)

    def get_base_params(self, parameters, context):
        provider: ProviderSetting = self.providers[
            self.parameterAsEnum(parameters, self.IN_PROVIDER, context)
        ]
        mode = RoutingMetric[self.parameterAsEnum(parameters, self.IN_MODE, context)]
        # url = self.parameterAsString(parameters, self.IN_URL, context)
        # pkg = (
        #     self.pkgs[self.parameterAsEnum(parameters, self.IN_PKG, context)]
        #     if len(self.pkgs) > 0
        #     else ""
        # )
        layer_1: QgsFeatureSource = self.parameterAsSource(parameters, self.IN_1, context)
        layer_field_name_1 = self.parameterAsString(parameters, self.IN_FIELD_1, context)

        params = dict()
        if self.router == RouterType.VALHALLA and self.WITH_COSTING_OPTIONS:
            params["options"] = dict()
            avoid_locations = self.parameterAsSource(parameters, self.IN_AVOID_LOCATIONS, context)
            if avoid_locations:
                params["avoid_locations"] = get_wgs_coords_from_layer(avoid_locations)

            avoid_polygons = self.parameterAsSource(parameters, self.IN_AVOID_POLYGONS, context)
            if avoid_polygons:
                params["avoid_polygons"] = get_wgs_coords_from_layer(avoid_polygons)

            linear_factors = self.parameterAsSource(parameters, self.IN_LINEAR_FACTORS, context)
            if linear_factors:
                factor_field = self.parameterAsString(parameters, self.IN_LINEAR_FACTORS_FIELD, context)
                if not factor_field:
                    raise QgsProcessingException(
                        "The layer with map matched lines needs a factor field to be selected."
                    )
                params["linear_cost_factors"] = get_linear_cost_factors(linear_factors, factor_field)

            params["options"] = self.get_costing_options(parameters, context)
            params["options"]["shortest"] = True if mode == RoutingMetric.SHORTEST else False

        factory_args = {
            "provider": self.router,
            "url": provider.url,
            "profile": self.profile,
            "method": RouterMethod.REMOTE,
        }

        # if method == RouterMethod.LOCAL:
        #     if not pkg:
        #         raise QgsProcessingException(
        #             f"You chose the '{method}' method but don't seem to have any packages downloaded."
        #         )
        #     factory_args.update(
        #         {
        #             "method": RouterMethod.LOCAL,
        #             "url": None,
        #             "pkg_path": str(get_graph_dir(self.router) / pkg),
        #         }
        #     )
        results_factory = ResultsFactory(**factory_args)

        return layer_1, layer_field_name_1, params, results_factory

    def setup_costing_options(self):
        """Takes the costing defaults retrieved from the settings widget and creates processing parameters from them."""
        for param_name, param_dict in self.costing_defaults:
            processing_param: QgsProcessingParameterDefinition

            processing_param_args = {
                "name": param_name,
                "description": " ".join(map(lambda w: w.capitalize(), param_name.split("_"))),
                "defaultValue": param_dict["value"],
                "optional": True,
            }

            param_type = param_dict["type"]

            if param_type in (int, float):
                param_min = param_dict["min"]
                param_max = param_dict["max"]

                processing_param = QgsProcessingParameterNumber(
                    **processing_param_args,
                    type=(
                        QgsProcessingParameterNumber.Type.Integer
                        if param_type == int
                        else QgsProcessingParameterNumber.Type.Double
                    ),
                    minValue=param_min,
                    maxValue=param_max,
                )
            elif param_type == bool:
                processing_param = QgsProcessingParameterBoolean(**processing_param_args)

            elif param_type == str:
                processing_param = QgsProcessingParameterString(**processing_param_args)

            else:
                raise TypeError(f"Type {param_type} not supported for costing options.")

            processing_param.setFlags(
                processing_param.flags() | QgsProcessingParameterDefinition.Flag.FlagAdvanced
            )
            processing_param.setHelp(param_dict["help"])
            self.addParameter(processing_param)

    @staticmethod
    def get_costing_defaults(costing_widget: Type[ValhallaSettingsBase]):
        """Retrieves the costing defaults from the given costing_widgets."""
        return costing_widget().get_params(include_info=True).items()

    def get_costing_options(self, parameters, context) -> dict:
        """Returns the costing options as set by the user in the processing algo UI."""
        costing_params = dict()
        for param_name, param_dict in self.costing_defaults:
            param_type = param_dict["type"]

            if param_type == int:
                parameter_getter = self.parameterAsInt
            elif param_type == float:
                parameter_getter = self.parameterAsDouble
            elif param_type == bool:
                parameter_getter = self.parameterAsBoolean
            elif param_type == str:
                parameter_getter = self.parameterAsString
            else:
                raise TypeError(f"Type {param_type} not supported for costing options.")
            costing_params[param_name] = parameter_getter(parameters, param_name, context)

        return costing_params

    def get_feature_sink(self, parameters, context, fields: QgsFields) -> Tuple[QgsFeatureSink, str]:
        """
        Convenience method to get the algorithm's feature sink with the corresponding endpoint's QgsFields
        and optionally additional fields (e.g. an ID field).

        :param parameters: passed from processAlgorithm()
        :param context: passed from processAlgorithm()
        :param fields: QgsFields that will be appended after the endpoint fields or overwrite the default if specified twice.
        """

        return self.parameterAsSink(
            parameters,
            self.OUT,
            context,
            fields,
            ResultsFactory.geom_type(self.endpoint),
            WGS84,
        )

    def createInstance(self):
        return type(self)()

    def icon(self) -> QIcon:
        return get_icon(f"{self.endpoint.lower()}_icon.svg")

    def name(self):
        if self.router == RouterType.VALHALLA:
            if self.endpoint == RouterEndpoint.ELEVATION:
                return "valhalla_elevation"
            return f"{self.router.lower()}_{self.endpoint.lower()}_{self.profile.lower()}"

        return f"{self.router.lower()}_{self.endpoint.lower()}"

    def displayName(self):
        if self.router == RouterType.VALHALLA:
            if self.endpoint == RouterEndpoint.ELEVATION:
                return "Elevation"
            return self.profile.capitalize()

        return self.endpoint.capitalize()  # there are no profiles for OSRM

    def shortHelpString(self):
        """Displays the sidebar help in the algorithm window"""

        file = HELP_DIR / Path(f"{self.router.lower()}_{self.endpoint.lower()}.help")

        with open(file) as fh:
            msg = fh.read()

        return msg
