"""
Runs a spatial optimization from the dock: the matrix is requested through the dock's results
factory, the solve runs as the Processing algorithm in a QgsProcessingAlgRunnerTask, so it's
in QGIS' task manager and can be canceled there.
"""

from typing import Callable, Dict, List, Optional, Tuple, Type

from qgis.core import (
    QgsApplication,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMapLayer,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingOutputLayerDefinition,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant

from ..core.results_factory import ResultsFactory
from ..core.spopt import SpoptProblem
from ..global_definitions import DEFAULT_LAYER_FIELDS, RouterEndpoint
from ..processing.spatial_optimization.base_algorithm import SpoptBaseAlgorithm
from ..processing.spatial_optimization.lscp import LSCPAlgorithm
from ..processing.spatial_optimization.mclp import MCLPAlgorithm
from ..utils.logger_utils import qgis_log
from .widgets.waypoint_model import SpoptRole, Waypoint

ALGORITHMS: Dict[SpoptProblem, Type[SpoptBaseAlgorithm]] = {
    SpoptProblem.LSCP: LSCPAlgorithm,
    SpoptProblem.MCLP: MCLPAlgorithm,
}

# both point layers are joined to the matrix by this field: the point's index in its layer,
# which is also what the matrix result has as source/target
ID_FIELD = "id"


class _Feedback(QgsProcessingFeedback):
    """Keeps the errors for the message bar, the details go to the log."""

    def __init__(self):
        super().__init__()
        self.errors: List[str] = list()

    def reportError(self, error: str, fatalError: bool = False):
        self.errors.append(error)
        super().reportError(error, fatalError)

    def pushDebugInfo(self, info: str):
        qgis_log(info)
        super().pushDebugInfo(info)


def split_waypoints(waypoints: List[Waypoint]) -> Tuple[List[Waypoint], List[Waypoint]]:
    """The spopt table's points as (facilities, demand points)."""
    facilities = [wp for wp in waypoints if wp.attrs.get("role") == SpoptRole.FACILITY.value]
    demand = [wp for wp in waypoints if wp.attrs.get("role") != SpoptRole.FACILITY.value]
    return facilities, demand


def points_layer(name: str, waypoints: List[Waypoint], extra: QgsField) -> QgsVectorLayer:
    """A WGS84 memory layer with an ID, the name and ``extra`` (weight or predefined)."""
    layer = QgsVectorLayer("Point?crs=EPSG:4326", name, "memory")
    layer.dataProvider().addAttributes(
        [QgsField(ID_FIELD, QVariant.Int), QgsField("name", QVariant.String), extra]
    )
    layer.updateFields()

    feats = list()
    for idx, wp in enumerate(waypoints):
        feat = QgsFeature(layer.fields())
        value = wp.attrs.get(extra.name())
        if extra.type() == QVariant.Int:  # predefined is a bool in the table
            value = int(bool(value))
        feat.setAttributes([idx, wp.attrs.get("name") or None, value])
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(wp.lon, wp.lat)))
        feats.append(feat)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()

    return layer


def matrix_layer(
    factory: ResultsFactory, facilities: List[Waypoint], demand: List[Waypoint], params: dict
) -> QgsVectorLayer:
    """The facilities x demand points matrix, source/target are the indices into either list."""
    layer = QgsVectorLayer("None", "spopt matrix", "memory")
    fields = QgsFields()
    for field in DEFAULT_LAYER_FIELDS[RouterEndpoint.MATRIX]:
        fields.append(QgsField(field))
    layer.dataProvider().addAttributes(fields)
    layer.updateFields()

    locations = [(wp.lon, wp.lat) for wp in facilities + demand]
    params = {
        **params,
        "sources": list(range(len(facilities))),
        "destinations": list(range(len(facilities), len(locations))),
    }
    layer.dataProvider().addFeatures(
        list(factory.get_results(RouterEndpoint.MATRIX, locations, params, fields))
    )

    return layer


class SpoptRun:
    """
    One solve in the background. Keep a reference until ``on_done`` was called: it owns the
    input layers, the context with the results and the task.

    ``on_done(layers, error)`` gets the facilities & demand output layers, or an error message
    (None if it was canceled).
    """

    def __init__(
        self,
        problem: SpoptProblem,
        facilities: List[Waypoint],
        demand: List[Waypoint],
        matrix: QgsVectorLayer,
        metric_idx: int,
        service_radius: float,
        n_facilities: int,
        draw_lines: bool,
        on_done: Callable[[List[QgsMapLayer], Optional[str]], None],
    ):
        self.problem = problem
        self.on_done = on_done
        alg_cls = ALGORITHMS[problem]

        self.fac_layer = points_layer("facilities", facilities, QgsField("predefined", QVariant.Int))
        self.dem_layer = points_layer("demand", demand, QgsField("weight", QVariant.Double))
        self.matrix = matrix

        self.params = {
            alg_cls.IN_MATRIX: self.matrix,
            alg_cls.IN_METRIC: metric_idx,
            alg_cls.IN_FAC: self.fac_layer,
            alg_cls.IN_FAC_ID: ID_FIELD,
            alg_cls.IN_DEM: self.dem_layer,
            alg_cls.IN_DEM_ID: ID_FIELD,
            alg_cls.IN_SERVICE_RADIUS: service_radius,
            alg_cls.IN_PREDEFINED: "predefined",
            alg_cls.IN_LINES: draw_lines,
            alg_cls.OUT_FAC: QgsProcessingOutputLayerDefinition(QgsProcessing.TEMPORARY_OUTPUT),
            alg_cls.OUT_DEM: QgsProcessingOutputLayerDefinition(QgsProcessing.TEMPORARY_OUTPUT),
        }
        if problem == SpoptProblem.MCLP:
            self.params[MCLPAlgorithm.IN_N_FAC] = n_facilities
            self.params[MCLPAlgorithm.IN_WEIGHTS] = "weight"

        self.alg = alg_cls().create()
        self.context = QgsProcessingContext()
        self.context.setProject(QgsProject.instance())
        self.feedback = _Feedback()
        self.task = QgsProcessingAlgRunnerTask(self.alg, self.params, self.context, self.feedback)
        self.task.executed.connect(self._on_executed)

    def start(self):
        QgsApplication.taskManager().addTask(self.task)

    def cancel(self):
        self.task.cancel()

    def _on_executed(self, successful: bool, results: dict):
        if not successful:
            if self.feedback.isCanceled():
                self.on_done([], None)
            else:
                self.on_done([], self.feedback.errors[-1] if self.feedback.errors else "Unknown error")
            return

        layers = list()
        for key, what in ((self.alg.OUT_FAC, "Facilities"), (self.alg.OUT_DEM, "Demand")):
            layer = self.context.takeResultLayer(results[key])
            layer.setName(f"{self.problem.value.upper()} {what}")
            layers.append(layer)
        self.on_done(layers, None)
