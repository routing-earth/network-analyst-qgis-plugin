import json
from typing import Dict, List, Optional, Union

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsClassificationEqualInterval,
    QgsColorRamp,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureRequest,
    QgsGraduatedSymbolRenderer,
    QgsJsonExporter,
    QgsProcessingFeatureSource,
    QgsRendererCategory,
    QgsRendererRange,
    QgsSingleSymbolRenderer,
    QgsStyle,
    QgsSymbol,
    QgsVariantUtils,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..global_definitions import RouterEndpoint
from .geom_utils import encode_polyline6
from .resource_utils import get_resource_path

COLOR_RAMP: QgsColorRamp = QgsStyle().defaultStyle().colorRamp("GnBu")

STYLE_FIELDS: Dict[RouterEndpoint, Optional[str]] = {
    RouterEndpoint.DIRECTIONS: "duration",
    RouterEndpoint.ISOCHRONES: "contour",
    RouterEndpoint.MATRIX: None,
    RouterEndpoint.EXPANSION: None,
    RouterEndpoint.ELEVATION: None,
}


class STYLES:
    COLORS = list(reversed([COLOR_RAMP.color(x * 0.1) for x in range(10)]))
    SINGLE_COLOR = COLOR_RAMP.color(1)
    LINE_WIDTH = 0.6
    POLYGON_OPACITY = 0.7


class Interpolator:
    """Class for mapping two iterables for consistent coloring of
    multiple isochrone/direction result features within one layer."""

    def __init__(self, left_iter, right_iter):
        self.left_min = 0
        self.right_min = 0
        self.left_span = len(left_iter) - 1
        self.right_span = len(right_iter) - 1
        try:
            self.scale_factor = float(self.right_span) / float(self.left_span)
        except ZeroDivisionError:
            self.scale_factor = self.right_span

    def interpolate(self, value):
        return min(
            max(self.right_span, self.left_span),
            int(self.right_min + (value - self.left_min) * self.scale_factor),
        )


def get_wgs_coords_from_feature(feature: QgsFeature, crs: QgsCoordinateReferenceSystem):
    exporter = QgsJsonExporter()
    exporter.setSourceCrs(crs)  # needed for automatic conversion to WGS84
    json_feature = json.loads(exporter.exportFeature(feature))
    return json_feature["geometry"]["coordinates"]


def get_wgs_coords_from_layer(
    layer: Union[QgsVectorLayer, QgsProcessingFeatureSource],
    order_by: Optional[str] = None,
) -> list:
    """
    Turns the input layer's geometries into lists of coordinates for use with Valhalla's
    locations, exclude_locations and exclude_polygons parameters. Handles point and polygon layers of single and multi
    type geometries.

    :param layer: A QgsVectorLayer or QgsProcessingFeatureSource
    :param order_by: A field name to order the layer's features by
    """
    if not layer.featureCount():
        return []

    # yes, this is cumbersome, but QgsFeatureSource is missing .geometryType()
    is_poly = layer.wkbType() in (
        Qgis.WkbType.Polygon,
        Qgis.WkbType.MultiPolygon,
        Qgis.WkbType.Polygon25D,
        Qgis.WkbType.PolygonM,
        Qgis.WkbType.PolygonZ,
        Qgis.WkbType.PolygonZM,
        Qgis.WkbType.MultiPolygon25D,
        Qgis.WkbType.MultiPolygonM,
        Qgis.WkbType.MultiPolygonZ,
        Qgis.WkbType.MultiPolygonZM,
    )
    is_line = layer.wkbType() in (
        Qgis.WkbType.LineString,
        Qgis.WkbType.MultiLineString,
        Qgis.WkbType.LineString25D,
        Qgis.WkbType.LineStringM,
        Qgis.WkbType.LineStringZ,
        Qgis.WkbType.LineStringZM,
        Qgis.WkbType.MultiLineString25D,
        Qgis.WkbType.MultiLineStringM,
        Qgis.WkbType.MultiLineStringZ,
        Qgis.WkbType.MultiLineStringZM,
    )
    is_point = layer.wkbType() in (
        Qgis.WkbType.Point,
        Qgis.WkbType.MultiPoint,
        Qgis.WkbType.Point25D,
        Qgis.WkbType.PointM,
        Qgis.WkbType.PointZ,
        Qgis.WkbType.PointZM,
        Qgis.WkbType.MultiPoint25D,
        Qgis.WkbType.MultiPointM,
        Qgis.WkbType.MultiPointZ,
        Qgis.WkbType.MultiPointZM,
    )
    is_multi = QgsWkbTypes.isMultiType(layer.wkbType())
    coordinates = []

    exporter = QgsJsonExporter()
    crs = layer.dataProvider().sourceCrs() if isinstance(layer, QgsVectorLayer) else layer.sourceCrs()
    exporter.setSourceCrs(crs)  # needed for automatic conversion to WGS84

    order_by_clause = QgsFeatureRequest()

    if order_by:
        order_by_clause.addOrderBy(order_by)

    for feature in layer.getFeatures(order_by_clause):
        json_feature = json.loads(exporter.exportFeature(feature))
        coords = json_feature["geometry"]["coordinates"]

        # outer rings only for polygons
        if is_multi and is_poly:
            _ = [coordinates.append(coord[0]) for coord in coords]
        elif is_multi and is_point:
            _ = [coordinates.append(coord) for coord in coords]
        elif is_multi and is_line:
            _ = [coordinates.append(coord) for coord in coords]
        elif not is_multi and is_poly:
            coordinates.append(coords[0])
        elif not is_multi and is_point:
            coordinates.append(coords)
        elif not is_multi and is_line:
            _ = [coordinates.append(coord) for coord in coords]

    return coordinates


def get_linear_cost_factors(
    layer: Union[QgsVectorLayer, QgsProcessingFeatureSource], factor_field: str
) -> List[dict]:
    """
    Turns the input line layer into Valhalla's linear_cost_factors parameter: one object with a
    polyline6 encoded "shape" and a "factor" per line (each part of a multi line gets its feature's
    factor). The geometries must already be map matched. Features without a
    geometry or without a factor value are skipped.

    :param layer: A QgsVectorLayer or QgsProcessingFeatureSource with (multi) line geometries
    :param factor_field: The name of the numeric field holding the factor
    """
    linear_cost_factors = []
    crs = layer.sourceCrs()

    for feature in layer.getFeatures():
        factor = feature[factor_field]
        if not feature.hasGeometry() or QgsVariantUtils.isNull(factor):
            continue

        coords = get_wgs_coords_from_feature(feature, crs)
        lines = coords if QgsWkbTypes.isMultiType(feature.geometry().wkbType()) else [coords]
        for line in lines:
            linear_cost_factors.append({"shape": encode_polyline6(line), "factor": float(factor)})

    return linear_cost_factors


def post_process_layer(layer: QgsVectorLayer, endpoint: RouterEndpoint) -> None:  # noqa: C901
    """
    Updates a directions/isochrones result layer's symbology to the predefined style.

    :param layer: the layer to be updated
    :param endpoint: the endpoint which was called
    """
    if endpoint == RouterEndpoint.MATRIX:
        return
    elif endpoint in (RouterEndpoint.DIRECTIONS, RouterEndpoint.TSP, RouterEndpoint.MAP_MATCH):
        layer.loadNamedStyle(str(get_resource_path("styles", "directions.qml")))
        return

    color_field = STYLE_FIELDS[endpoint]

    if color_field:
        field: int = layer.fields().lookupField(color_field)
        unique_values = sorted(layer.uniqueValues(field))

        if layer.wkbType() == Qgis.WkbType.Polygon:
            layer.setOpacity(STYLES.POLYGON_OPACITY)
        if color_field == "contour":
            unique_values = list(reversed(unique_values))

        interpolator = Interpolator(unique_values, STYLES.COLORS)
        categories = []

        for id_, unique_value in enumerate(unique_values):
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            color = STYLES.COLORS[interpolator.interpolate(id_)]
            symbol.setColor(color)
            if layer.wkbType() == Qgis.WkbType.LineString:
                symbol.setWidth(STYLES.LINE_WIDTH)
            category = QgsRendererCategory(unique_value, symbol, str(unique_value))
            categories.append(category)

        renderer = QgsCategorizedSymbolRenderer(color_field, categories)
    elif endpoint == RouterEndpoint.EXPANSION:
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(STYLES.SINGLE_COLOR)
        symbol.setWidth(STYLES.LINE_WIDTH)
        renderer = QgsSingleSymbolRenderer(symbol)
    elif endpoint == RouterEndpoint.ELEVATION:
        # use expression to extract z value from QgsPoint
        color_ramp = QgsStyle().defaultStyle().colorRamp("Reds")
        expression = "z($geometry)"

        # Number of classes
        num_classes = min(5, layer.featureCount())
        # Compute class breaks using QGIS classification method
        renderer_tmp = QgsGraduatedSymbolRenderer()
        renderer_tmp.setClassAttribute(expression)
        renderer_tmp.setClassificationMethod(QgsClassificationEqualInterval())
        renderer_tmp.updateClasses(layer, num_classes)

        # Manually assign ramp colors to new ranges
        ranges = []
        classes = renderer_tmp.ranges()

        for i, rng in enumerate(classes):
            t = i / (len(classes) - 1) if len(classes) > 1 else 0
            color = color_ramp.color(t)

            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            symbol.setColor(color)

            new_range = QgsRendererRange(rng.lowerValue(), rng.upperValue(), symbol, rng.label())
            ranges.append(new_range)

        # Create new renderer with color-applied ranges
        renderer = QgsGraduatedSymbolRenderer(expression, ranges)
        renderer.setClassificationMethod(QgsClassificationEqualInterval())

    layer.setRenderer(renderer)
    layer.triggerRepaint()
