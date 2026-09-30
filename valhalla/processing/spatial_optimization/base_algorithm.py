import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSource,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from ...core import spopt
from ...core.spopt import SpoptProblem
from ...exceptions import SpoptError
from ...global_definitions import FieldNames
from ...utils.misc_utils import wrap_in_html_tag
from ...utils.resource_utils import get_icon
from ..processing_definitions import HELP_DIR


class SpoptBaseAlgorithm(QgsProcessingAlgorithm):
    """
    Base for the pysal/spopt location problems. Takes a cost matrix layer as produced by the
    matrix algorithms (facilities = sources, demand points = targets), solves it out of process
    (core/spopt) and joins the result back onto the facility & demand point layers.

    Subclasses set PROBLEM and add their parameters via init_problem_params/get_problem_kwargs.
    """

    PROBLEM: SpoptProblem

    IN_MATRIX = "INPUT_MATRIX_LAYER"
    IN_METRIC = "INPUT_METRIC"
    IN_FAC = "INPUT_FAC_LAYER"
    IN_FAC_ID = "INPUT_FAC_ID"
    IN_DEM = "INPUT_DEM_POINT_LAYER"
    IN_DEM_ID = "INPUT_DEM_ID"
    IN_LINES = "INPUT_LINES"

    OUT_FAC = "OUTPUT_FAC"
    OUT_DEM = "OUTPUT_DEM"

    METRICS = (FieldNames.DURATION, FieldNames.DISTANCE)

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def initAlgorithm(self, configuration, p_str=None, Any=None, *args, **kwargs):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                name=self.IN_MATRIX,
                description=f"{wrap_in_html_tag('Cost matrix', 'b')}. "
                "Output of a matrix algorithm with facilities as sources and demand points as targets",
                types=[QgsProcessing.SourceType.TypeVector],
            )
        )
        metric_param = QgsProcessingParameterEnum(
            name=self.IN_METRIC,
            description="Cost metric to optimize for (duration in seconds, distance in meters)",
            options=[m.value for m in self.METRICS],
            defaultValue=0,
        )
        metric_param.setFlags(metric_param.flags() | QgsProcessingParameterDefinition.Flag.FlagAdvanced)
        self.addParameter(metric_param)

        for layer_name, field_name, what in (
            (self.IN_FAC, self.IN_FAC_ID, "Facility"),
            (self.IN_DEM, self.IN_DEM_ID, "Demand point"),
        ):
            self.addParameter(
                QgsProcessingParameterFeatureSource(
                    name=layer_name,
                    description=f"{what} layer, as used for the matrix",
                    types=[QgsProcessing.SourceType.TypeVectorPoint],
                )
            )
            self.addParameter(
                QgsProcessingParameterField(
                    name=field_name,
                    description=f"{what} ID field, as used for the matrix (feature IDs if empty)",
                    parentLayerParameterName=layer_name,
                    optional=True,
                )
            )

        # after the layers: problem params may be fields of them
        self.init_problem_params()

        lines_param = QgsProcessingParameterBoolean(
            name=self.IN_LINES,
            description="Draw lines from demand points to their facilities",
            defaultValue=False,
        )
        lines_param.setFlags(lines_param.flags() | QgsProcessingParameterDefinition.Flag.FlagAdvanced)
        self.addParameter(lines_param)

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                name=self.OUT_FAC, description=f"{self.name()}_facilities", createByDefault=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                name=self.OUT_DEM, description=f"{self.name()}_demand", createByDefault=True
            )
        )

    def init_problem_params(self) -> None:
        """Adds the problem-specific parameters."""
        raise NotImplementedError

    def get_problem_kwargs(
        self,
        parameters,
        context,
        fac_ids: List[Any],
        dem_ids: List[Any],
        fac_feats: Dict[Any, QgsFeature],
        dem_feats: Dict[Any, QgsFeature],
    ) -> Dict[str, Any]:
        """The problem-specific kwargs for core.spopt.solve(), per-feature values in matrix order."""
        raise NotImplementedError

    def processAlgorithm(  # noqa: C901
        self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback
    ):
        matrix = self.parameterAsSource(parameters, self.IN_MATRIX, context)
        metric = self.METRICS[self.parameterAsEnum(parameters, self.IN_METRIC, context)]
        draw_lines = self.parameterAsBoolean(parameters, self.IN_LINES, context)

        # layer_ids are the same order as in the feature
        fac_layer_ids, dem_layer_ids, cost_matrix = self._read_matrix(matrix, metric)

        fac_source = self.parameterAsSource(parameters, self.IN_FAC, context)
        dem_source = self.parameterAsSource(parameters, self.IN_DEM, context)
        for source, param_name in ((fac_source, self.IN_FAC), (dem_source, self.IN_DEM)):
            if source is None:
                raise QgsProcessingException(self.invalidSourceError(parameters, param_name))

        fac_id_field = self.parameterAsString(parameters, self.IN_FAC_ID, context)
        fac_feats = self._index_features(fac_source, fac_id_field, fac_layer_ids, "facility")
        dem_id_field = self.parameterAsString(parameters, self.IN_DEM_ID, context)
        dem_feats = self._index_features(dem_source, dem_id_field, dem_layer_ids, "demand point")
        feedback.setProgress(20)

        try:
            fac2cli = spopt.solve(
                self.PROBLEM,
                cost_matrix,
                is_canceled=feedback.isCanceled,
                **self.get_problem_kwargs(
                    parameters, context, fac_layer_ids, dem_layer_ids, fac_feats, dem_feats
                ),
            )
        except SpoptError as e:
            feedback.pushDebugInfo(e.detail)
            raise QgsProcessingException(str(e))
        if fac2cli is None:  # canceled
            return {}
        feedback.setProgress(90)

        # facilities output: the selected ones only, with the number of demand points they cover
        fac_fields = QgsFields()
        fac_fields.append(self._id_field(FieldNames.ID, fac_source, fac_id_field))
        fac_fields.append(QgsField(FieldNames.DEMAND_COUNT, QVariant.Int))
        fac_sink, fac_dest_id = self.parameterAsSink(
            parameters, self.OUT_FAC, context, fac_fields, fac_source.wkbType(), fac_source.sourceCrs()
        )

        # demand output: one feature per (facility, demand point) with its cost, coverage
        # problems may assign a demand point to several facilities
        dem_fields = QgsFields()
        dem_fields.append(self._id_field(FieldNames.ID, dem_source, dem_id_field))
        dem_fields.append(self._id_field(FieldNames.FACILITY_ID, fac_source, fac_id_field))
        dem_fields.append(QgsField(metric.value, QVariant.Double))
        dem_geom_type = QgsWkbTypes.Type.LineString if draw_lines else dem_source.wkbType()
        dem_crs = dem_source.sourceCrs()
        dem_sink, dem_dest_id = self.parameterAsSink(
            parameters, self.OUT_DEM, context, dem_fields, dem_geom_type, dem_crs
        )

        # the facility end of the lines, already in the demand points' CRS
        fac_points = dict()
        if draw_lines:
            transform = QgsCoordinateTransform(
                fac_source.sourceCrs(), dem_crs, context.transformContext()
            )
            fac_points = {i: transform.transform(f.geometry().asPoint()) for i, f in fac_feats.items()}

        for fac_idx, demand_pts in enumerate(fac2cli):
            if demand_pts is None:  # not selected
                continue
            # add facility feature
            fac_id = fac_layer_ids[fac_idx]
            fac_feat = QgsFeature(fac_fields)
            fac_feat.setAttributes([fac_id, len(demand_pts)])
            fac_feat.setGeometry(fac_feats[fac_id].geometry())
            fac_sink.addFeature(fac_feat)

            # for each demand point, add a demand point feature
            for dem_idx in demand_pts:
                dem_id = dem_layer_ids[dem_idx]
                dem_feat = QgsFeature(dem_fields)
                dem_feat.setAttributes([dem_id, fac_id, cost_matrix[dem_idx][fac_idx]])
                if draw_lines:
                    dem_point = dem_feats[dem_id].geometry().asPoint()
                    dem_feat.setGeometry(QgsGeometry.fromPolylineXY([dem_point, fac_points[fac_id]]))
                else:
                    dem_feat.setGeometry(dem_feats[dem_id].geometry())
                dem_sink.addFeature(dem_feat)

        return {self.OUT_FAC: fac_dest_id, self.OUT_DEM: dem_dest_id}

    def _read_matrix(
        self, matrix: QgsFeatureSource, metric: FieldNames
    ) -> Tuple[List[Any], List[Any], List[List[float]]]:
        """
        Reads the matrix table layer as output by the Matrix algorithms.

        It returns the layers IDs for facilities (origins) and demands (destinations), and
        a transposed matrix which is what spopt expects. A missing pair or a NULL cost are
        modeles as infinity.
        """
        # check we got the fields we expect
        field_names = matrix.fields().names()
        if missing := [
            f for f in (FieldNames.SOURCE, FieldNames.TARGET, metric) if f not in field_names
        ]:
            raise QgsProcessingException(
                f"The cost matrix needs the fields {', '.join(missing)}, use a matrix algorithm's output"
            )

        # extract the layer ids of both and keep the cost values for later transposition
        costs: Dict[Tuple[Any, Any], float] = dict()
        fac_layer_ids: Dict[Any, None] = dict()
        dem_layer_ids: Dict[Any, None] = dict()
        for feat in matrix.getFeatures():
            fac_id, dem_id = feat[FieldNames.SOURCE], feat[FieldNames.TARGET]
            if (fac_id, dem_id) in costs:
                # e.g. the ID field used for the matrix wasn't unique
                raise QgsProcessingException(
                    f"The cost matrix has more than one row for source {fac_id} and target {dem_id}"
                )
            fac_layer_ids[fac_id] = None
            dem_layer_ids[dem_id] = None
            try:
                costs[(fac_id, dem_id)] = float(feat[metric])
            except (TypeError, ValueError):
                costs[(fac_id, dem_id)] = math.inf  # NULL

        if not any(math.isfinite(cost) for cost in costs.values()):
            raise QgsProcessingException("The cost matrix has no valid results")

        # transpose the cost matrix for spopt
        cost_matrix = [[costs.get((f, d), math.inf) for f in fac_layer_ids] for d in dem_layer_ids]

        return list(fac_layer_ids), list(dem_layer_ids), cost_matrix

    @staticmethod
    def _index_features(
        source: QgsFeatureSource, id_field: str, ids: List[Any], what: str
    ) -> Dict[Any, QgsFeature]:
        """The layer's features by ID (or feature ID); every matrix ID must be found exactly once."""
        feats: Dict[Any, QgsFeature] = dict()
        duplicates = set()
        for feat in source.getFeatures():
            feat_id = feat[id_field] if id_field else feat.id()
            if feat_id in feats:
                duplicates.add(feat_id)
            feats[feat_id] = feat

        if ambiguous := [str(i) for i in ids if i in duplicates]:
            raise QgsProcessingException(
                f"The {what} layer has several features for the matrix IDs {', '.join(ambiguous[:10])}"
                f"{' ...' if len(ambiguous) > 10 else ''}. The ID field must be unique."
            )
        if missing := [str(i) for i in ids if i not in feats]:
            raise QgsProcessingException(
                f"The {what} layer has no features for the matrix IDs {', '.join(missing[:10])}"
                f"{' ...' if len(missing) > 10 else ''}. Is it the same layer & ID field as for the matrix?"
            )

        return feats

    @staticmethod
    def _id_field(name: str, source: QgsFeatureSource, id_field: str) -> QgsField:
        """An ID field of the same type as the joined layer's ID field, feature IDs are ints."""
        if id_field:
            field = QgsField(source.fields().field(id_field))
            field.setName(name)
            return field

        return QgsField(name, QVariant.LongLong)

    def createInstance(self):
        return type(self)()

    def group(self):
        return "Spatial Optimization"

    def groupId(self):
        return "valhalla_spatial_optimization"

    def icon(self) -> QIcon:
        return get_icon("matrix_icon.svg")

    def name(self):
        return self.PROBLEM.value

    def shortHelpString(self):
        with open(HELP_DIR / Path(f"{self.PROBLEM.value}.help")) as fh:
            return fh.read()
