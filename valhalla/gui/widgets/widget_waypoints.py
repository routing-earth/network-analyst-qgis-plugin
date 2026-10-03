import json
from collections import defaultdict
from enum import Enum, unique
from typing import Any, DefaultDict, Dict, Generator, List, Optional, Tuple, Union
from urllib.parse import parse_qsl, urlparse

from qgis.core import (
    Qgis,
    QgsAnnotationLayer,
    QgsAnnotationMarkerItem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsLayerTreeNode,
    QgsMarkerSymbol,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsSvgMarkerSymbolLayer,
    QgsUnitTypes,
)
from qgis.gui import QgisInterface, QgsMapTool
from qgis.PyQt.QtCore import QPointF, QSize
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLayout,
    QMenu,
    QSizePolicy,
    QSpacerItem,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...global_definitions import RouterType
from ...gui.dlg_from_lyr import FromLayerDialog
from ...gui.maptools import PointTool
from ...third_party.routingpy.routingpy import Valhalla
from ...utils.geom_utils import point_to_wgs84
from ...utils.misc_utils import str_is_bool, str_is_float, str_to_bool
from ...utils.resource_utils import get_icon, get_resource_path
from ..dlg_from_json import FromValhallaJsonDialog
from ..dlg_from_osrm_url import FromOsrmUrlDialog
from .waypoint_model import (  # noqa: F401 - LocationType/PreferredSide re-exported
    KINDS,
    ROUTING,
    Column,
    ColumnKind,
    LocationType,
    PreferredSide,
    Waypoint,
    WaypointDelegate,
    WaypointTableKind,
    WaypointTableModel,
)

# where the points of each table kind are kept in the project
PROJECT_SCOPE = "valhalla"


@unique
class WayPtWidgetElems(Enum):
    ADD_PT = "ui_btn_add_pt"
    DEL_PT = "ui_btn_rm_pt"
    DEL_ALL = "ui_btn_rm_all"
    FROM_LYR = "ui_btn_from_lyr"
    FROM_JSON = "ui_btn_from_valhalla_json"
    FROM_URL = "ui_btn_from_osrm_url"
    SHOW_POINTS = "ui_btn_points"
    MV_UP = "ui_btn_up"
    MV_DOWN = "ui_btn_down"
    TABLE = "ui_table"


BUTTONS = {
    WayPtWidgetElems.ADD_PT: (
        ":images/themes/default/mActionAdd.svg",
        "Add points from the canvas, double-click when finished",
    ),
    WayPtWidgetElems.DEL_PT: (
        ":images/themes/default/symbologyRemove.svg",
        "Remove selected points",
    ),
    WayPtWidgetElems.DEL_ALL: (
        ":images/themes/default/mActionRemove.svg",
        "Clear all points",
    ),
    WayPtWidgetElems.FROM_LYR: (
        ":images/themes/default/mActionAddLayer.svg",
        "Add points from a layer",
    ),
    WayPtWidgetElems.FROM_JSON: (
        "valhalla_logo.svg",
        "Add points from a Valhalla locations JSON",
    ),
    WayPtWidgetElems.FROM_URL: (
        "osrm_logo.svg",
        "Add points from a OSRM URL",
    ),
    WayPtWidgetElems.MV_UP: (
        ":images/themes/default/mActionArrowUp.svg",
        "Move point up.",
    ),
    WayPtWidgetElems.MV_DOWN: (
        ":images/themes/default/mActionArrowDown.svg",
        "Move point down",
    ),
    WayPtWidgetElems.SHOW_POINTS: (
        ":images/themes/default/mActionCapturePoint.svg",
        "Show/hide waypoints",
    ),
}


def build_btn(layout: QLayout, btn_type: WayPtWidgetElems, checkable: bool = False) -> QToolButton:
    """Builds a button of configured type and adds it to the passed layout"""
    name = btn_type.value
    icon_path, tip = BUTTONS[btn_type]

    btn = QToolButton()
    btn.setCheckable(checkable)
    btn.setIcon(get_icon(icon_path))
    btn.setObjectName(name)
    btn.setIconSize(QSize(16, 16))
    btn.setToolTip(tip)

    layout.addWidget(btn)

    return btn


def extract_locations(  # noqa: C901
    router: RouterType, locations: Union[List[dict], str]
) -> Generator[Tuple[float, float, LocationType, PreferredSide, int, str], None, None]:
    """
    Extracts extra parameters being set on a OSRM URL or a Valhalla location JSON

    :param router: the router/provider type
    :param location: the whole valhalla location JSON or OSRM URL
    :returns: lat, lon, type, preferred_side, radius, extra_col
    :raises: ValueError
    """
    if router == RouterType.VALHALLA:
        for loc in locations:
            extra_col: List[str] = list()
            radius = 0
            type_ = LocationType.BREAK
            preferred_side = PreferredSide.EITHER
            for k, v in loc.items():
                if k in ("lat", "lon", "search_filter"):
                    continue
                elif k == "radius":
                    radius = v
                    continue
                elif k == "type":
                    type_ = v
                    continue
                elif k == "preferred_side":
                    preferred_side = v
                    continue
                extra_col.append(f"{k}={v}")

            yield loc["lat"], loc["lon"], type_, preferred_side, radius, "&".join(extra_col)
    else:
        # OSRM needs to be parsed from the URL
        parsed_url = urlparse(locations)
        # filter the parameters to only location-relevant ones and split the values
        params = {
            k: [y for y in v.split(";")]
            for k, v in filter(lambda x: x[0] in ("bearings", "radiuses"), parse_qsl(parsed_url.query))
        }
        locs = (
            locations.split("/")[-1].split("?")[0].split(";")
        )  # urlparse gets confused here with OSRM's location stuff in the path
        radiuses: List[str] = params.get("radiuses", list())

        if not all([len(x) == len(locs) for x in params.values()]):
            raise ValueError("Query parameters are not consistent with the amount of locations")

        for idx, loc in enumerate(locs):
            extra_col: str = ""
            if params.get("bearings"):
                # for now, we convert this to valhalla params
                extra_col += f"heading={params['bearings'][idx]}"
            lon, lat = [float(x) for x in loc.split(",")]

            radius = 0
            if len(radiuses) > 0:
                if radiuses[idx].isnumeric():
                    radius = int(radiuses[idx])

            yield lat, lon, LocationType.BREAK, PreferredSide.EITHER, radius, extra_col


def _plain(value: Any) -> Any:
    """An enum's value, anything else as is: str enums don't format to their value."""
    return value.value if isinstance(value, Enum) else value


def _from_field(column: Column, value: Any) -> Any:
    """A layer attribute converted for ``column``, None for NULL or what doesn't convert."""
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return None
    try:
        if column.kind == ColumnKind.BOOL:
            return float(value) == 1
        if column.kind == ColumnKind.INT:
            return int(value)
        if column.kind == ColumnKind.FLOAT:
            return float(value)
        return str(value)
    except (TypeError, ValueError):
        return None


class WaypointsWidget(QWidget):
    ANN_NAME = ROUTING.ann_layer_name

    def __init__(self, parent_dlg: QWidget = None, iface: QgisInterface = None):
        """
        Represents the waypoint table widget. Needs to be added to the parent's layout
        widget by the caller.

        :param parent_dlg: Parent dialog
        :param iface: The QGIS interface
        """
        super().__init__(parent_dlg)
        self.iface = iface
        self.parent_dlg = parent_dlg

        self.point_tool = PointTool(self.iface.mapCanvas())
        self.last_maptool: Optional[QgsMapTool] = None

        # every table kind has its own points, its own annotation layer and is kept in the project
        self.table_models: Dict[str, WaypointTableModel] = {
            n: WaypointTableModel(k, self) for n, k in KINDS.items()
        }
        self.table_model = self.table_models[ROUTING.name]
        self._ann_ids: Dict[str, Optional[str]] = {name: None for name in KINDS}
        # the attributes of points added from the canvas, set by the kind's add modes
        self._add_attrs: Dict[str, Any] = dict()
        # don't write back to the project while restoring from it
        self._restoring = False

        self.setupUi()

        for name, model in self.table_models.items():
            # only the shown model has editors
            model.rowsInserted.connect(
                lambda _, first, last, m=model: m is self.table_model
                and self._open_persistent_editors(first, last)
            )
            model.modelReset.connect(
                lambda m=model: m is self.table_model and self._open_persistent_editors()
            )
            for signal in (
                model.rowsInserted,
                model.rowsRemoved,
                model.rowsMoved,
                model.dataChanged,
                model.modelReset,
            ):
                signal.connect(lambda *_, n=name: self._store(n))
        self._setup_kind_buttons()

        # always look for existing points in the project before creating new ones; a new
        # project (cleared) has none, so the tables empty
        self._handle_read_project()
        self.iface.projectRead.connect(self._handle_read_project)
        QgsProject.instance().cleared.connect(self._handle_read_project)

    def unload(self):
        """Disconnects from the project, else a reloaded plugin's old widget keeps listening."""
        for signal in (self.iface.projectRead, QgsProject.instance().cleared):
            try:
                signal.disconnect(self._handle_read_project)
            except TypeError:
                pass

    @property
    def points_lyr_id(self) -> Optional[str]:
        """The shown kind's annotation layer ID."""
        return self._ann_ids[self.table_model.table_kind.name]

    @property
    def points_lyr(self) -> Optional[QgsAnnotationLayer]:
        """The shown kind's annotation layer, if it (still) exists."""
        return QgsProject.instance().mapLayer(self.points_lyr_id) if self.points_lyr_id else None

    def set_kind(self, kind: WaypointTableKind):
        """Shows the points of another table kind; the other kind's markers are hidden."""
        if kind is self.table_model.table_kind:
            return
        self._set_layer_visible(self.table_model.table_kind, False)
        self.table_model = self.table_models[kind.name]
        self.ui_table.setModel(self.table_model)
        self._open_persistent_editors()
        self._setup_kind_buttons()
        self._set_layer_visible(kind, True)

    @property
    def waypoints(self) -> List[Waypoint]:
        return self.table_model.waypoints

    def add_waypoint(self, lon: float, lat: float, row: Optional[int] = None, **attrs) -> int:
        """Adds a WGS84 point before ``row`` (default: at the end); returns its row."""
        row = self.table_model.rowCount() if row is None else row
        self.table_model.insert(row, [Waypoint(lon, lat, {k: _plain(v) for k, v in attrs.items()})])
        return row

    def get_locations(self, router: RouterType) -> List[Union[Tuple[float, float], Valhalla.Waypoint]]:
        """Convenience method for getting coordinate tuples of all routing table rows."""
        locations = list()
        for wp in self.table_models[ROUTING.name].waypoints:
            # for Valhalla we have to build routingpy Valhalla.Waypoint objects
            if router != RouterType.VALHALLA:
                locations.append([wp.lon, wp.lat])
                continue

            kwargs = dict()
            for k, v in parse_qsl(wp.attrs["extra"]):
                if v.isnumeric():
                    kwargs[k] = int(v)
                elif str_is_bool(v):
                    kwargs[k] = str_to_bool(v)
                elif str_is_float(v):
                    kwargs[k] = float(v)
                else:  # it's a string
                    kwargs[k] = v
            kwargs["type"] = wp.attrs["type"]
            kwargs["preferred_side"] = wp.attrs["preferred_side"]
            if wp.attrs["radius"] > 0:
                kwargs["radius"] = wp.attrs["radius"]
            locations.append(Valhalla.Waypoint([round(wp.lon, 6), round(wp.lat, 6)], **kwargs))

        return locations

    def get_extra_params(self, router: RouterType) -> Union[DefaultDict, dict]:
        """Returns OSRM parameters that are stored in the locations table"""
        if router == RouterType.VALHALLA:
            return {}

        params = defaultdict(list)
        for wp in self.table_models[ROUTING.name].waypoints:
            bearing = dict(parse_qsl(wp.attrs["extra"])).get("bearing", "360,180")
            params["bearings"].append([int(x) for x in bearing.split(",")])
            params["radiuses"].append(wp.attrs["radius"] or "unlimited")  # avoid radius=0

        return params

    def _add_extracted_row(self, locations: Generator[Tuple, None, None]):
        """Appends the rows of extract_locations()"""
        for lat, lon, loc_type, preferred_side, radius, extra in locations:
            self.add_waypoint(
                lon, lat, type=loc_type, preferred_side=preferred_side, radius=int(radius), extra=extra
            )

    def _handle_from_layer(self):
        """Fires a dialog to choose a Point layer and populates the waypoints table with the features."""
        dlg = FromLayerDialog(self.parent_dlg, self.table_model.table_kind)
        r = dlg.exec()

        if r == QDialog.DialogCode.Rejected or not dlg.layer or not dlg.layer.isValid():
            return

        columns = {c.key: c for c in self.table_model.table_kind.columns}
        feat: QgsFeature
        waypoints = list()
        for feat in dlg.layer.getFeatures():
            pt: QgsPointXY = point_to_wgs84(feat.geometry().asPoint(), dlg.layer.crs())
            attrs = dict(dlg.attrs)
            for key, field_name in dlg.field_map.items():
                if (value := _from_field(columns[key], feat[field_name])) is not None:
                    attrs[key] = value
            waypoints.append(Waypoint(pt.x(), pt.y(), attrs))
        self.table_model.append(waypoints)

        self._reset_annotations()

    def _handle_from_osrm_url(self):
        """Fill the locations table by parsing a OSRM URL"""
        url_dlg = FromOsrmUrlDialog(self.parent_dlg)
        r = url_dlg.exec()
        if r != QDialog.DialogCode.Accepted:
            return

        try:
            # parse everything before touching the table, a bad URL must not leave half of it
            self._add_extracted_row(extract_locations(RouterType.OSRM, url_dlg.ui_url.text()))
        except ValueError as e:
            self.parent_dlg.status_bar.pushMessage("OSRM Error", str(e), Qgis.MessageLevel.Critical, 8)

    def _handle_from_valhalla_json(self):
        """Fill the locations table by parsing a Valhalla locations JSON"""
        json_dlg = FromValhallaJsonDialog(self.parent_dlg)
        r = json_dlg.exec()
        if r != QDialog.DialogCode.Accepted:
            return

        try:
            json_obj = json.loads(json_dlg.json_field.toPlainText())
            if isinstance(json_obj, dict):
                json_obj = json_obj["locations"]
            elif not isinstance(json_obj, list):
                raise ValueError(
                    f"Passed JSON is not a Valhalla JSON request object or an array of Valhalla locations: {json_dlg.json_field.toPlainText()}"
                )
        except (json.JSONDecodeError, ValueError) as e:
            self.parent_dlg.status_bar.pushMessage("JSON Error", str(e), Qgis.MessageLevel.Critical, 8)
            return

        self._add_extracted_row(extract_locations(RouterType.VALHALLA, json_obj))

        self._reset_annotations()

    def _handle_clear_locations(self):
        """Clear the table and the annotations layer"""
        if self.points_lyr:
            self.points_lyr.clear()
        self.table_model.clear()

    def _store(self, kind_name: str):
        """Keeps a table kind's points in the project settings as JSON, as they're saved with it."""
        if self._restoring:
            return
        rows = [[wp.lon, wp.lat, wp.attrs] for wp in self.table_models[kind_name].waypoints]
        QgsProject.instance().writeEntry(PROJECT_SCOPE, f"waypoints/{kind_name}", json.dumps(rows))

    def _handle_read_project(self):
        """Restores every kind's points from the project and finds their annotation layers."""
        self._restoring = True
        try:
            for name, model in self.table_models.items():
                stored, ok = QgsProject.instance().readEntry(PROJECT_SCOPE, f"waypoints/{name}", "")
                try:
                    waypoints = [Waypoint(lon, lat, attrs) for lon, lat, attrs in json.loads(stored)]
                except (ValueError, TypeError):
                    # old project from before the points was stored: only routing had any back then
                    waypoints = self._routing_from_annotations() if name == ROUTING.name else []
                model.clear()
                model.append(waypoints)

                layers = QgsProject.instance().mapLayersByName(KINDS[name].ann_layer_name)
                self._ann_ids[name] = layers[0].id() if layers else None
                if layers:
                    self._attach_node_to_slot(name)
        finally:
            self._restoring = False

    def _routing_from_annotations(self) -> List[Waypoint]:
        """legacy function only used for projects < 6/7.2.0"""
        layers = QgsProject.instance().mapLayersByName(ROUTING.ann_layer_name)
        if not layers:
            return []
        waypoints = list()
        for _, item in layers[0].items().items():
            item: QgsAnnotationMarkerItem
            pt = point_to_wgs84(item.geometry(), self.iface.mapCanvas().mapSettings().destinationCrs())
            waypoints.append(Waypoint(pt.x(), pt.y()))
        return waypoints

    def _set_layer_visible(self, kind: WaypointTableKind, visible: bool):
        """Shows/hides a kind's annotation layer, if it exists."""
        layer_id = self._ann_ids[kind.name]
        node = QgsProject.instance().layerTreeRoot().findLayer(layer_id) if layer_id else None
        if node is not None:
            node.setItemVisibilityChecked(visible)
        if kind is self.table_model.table_kind:
            self.ui_btn_show_point_lyr.setChecked(node.isVisible() if node is not None else True)

    def _handle_points_btn_toggle(self, checked: bool):
        """hides/shows the waypoint layer in the canvas"""
        if not self.points_lyr:
            self._reset_annotations()
        QgsProject.instance().layerTreeRoot().findLayer(self.points_lyr.id()).setItemVisibilityChecked(
            checked
        )

    def _handle_points_layer_toggle(self, node: QgsLayerTreeNode):
        """gets a signal when a layer's visibility as been changed so we switch the button in the UI"""
        if not node.name() == self.table_model.table_kind.ann_layer_name:
            return

        self.ui_btn_show_point_lyr.setChecked(node.isVisible())

    def _move_current(self, delta: int):
        """Moves the current row up (-1) or down (+1) and keeps it current."""
        current = self.ui_table.currentIndex()
        if not current.isValid():
            return
        new_row = self.table_model.move(current.row(), delta)
        self.ui_table.setCurrentIndex(self.table_model.index(new_row, current.column()))
        self.ui_table.selectRow(new_row)

        self._reset_annotations()

    def _handle_pt_up(self):
        """Move a location up in the table."""
        self._move_current(-1)

    def _handle_pt_down(self):
        """Move a location down in the table."""
        self._move_current(1)

    def _start_adding(self, attrs: Dict[str, Any]):
        """Starts adding points from the canvas in an add mode, e.g. as facilities."""
        self._add_attrs = dict(attrs)
        self.ui_btn_add_pt.setChecked(True)
        self._handle_init_maptool()

    def _handle_init_maptool(self):
        """Set up the maptool: remember the last one used."""
        if self.ui_btn_add_pt.isChecked():
            # switching the add mode while adding: keep the tool we came from
            if self.iface.mapCanvas().mapTool() is not self.point_tool:
                self.last_maptool = self.iface.mapCanvas().mapTool()
            self.iface.mapCanvas().setMapTool(self.point_tool)

            # add a layer if not there already
            if not self.points_lyr:
                self._reset_annotations()
            # make sure it's also visible
            QgsProject.instance().layerTreeRoot().findLayer(
                self.points_lyr.id()
            ).setItemVisibilityChecked(True)
        else:
            self._handle_doubleclick()

    def _handle_add_pt(self, pt: QgsPointXY):
        """Transforms the clicked point and adds it to the table in the current add mode."""
        self.add_canvas_point(pt, self._add_attrs)

    def add_canvas_point(self, pt: QgsPointXY, attrs: Dict[str, Any]):
        """Adds a point in the canvas CRS after the current row, e.g. from the context menu."""
        new_pt = point_to_wgs84(pt, self.iface.mapCanvas().mapSettings().destinationCrs())
        current = self.ui_table.currentIndex()
        row_id = self.add_waypoint(
            new_pt.x(), new_pt.y(), current.row() + 1 if current.isValid() else None, **attrs
        )

        # select the new point in the table so the next clicked point goes to the end of the table
        self.ui_table.clearSelection()
        self.ui_table.selectRow(row_id)

        self._reset_annotations()

        # if from context menu, the dialog can be hidden but already have waypoints
        if self.parent_dlg.isHidden():
            self.parent_dlg.show()

    def _handle_doubleclick(self):
        """Shows the parent dlg again and restores previous settings"""
        self._reset_annotations()
        self.ui_btn_add_pt.setChecked(False)

        # then restore some of the things we set up when initializing the point tool
        QApplication.restoreOverrideCursor()
        QApplication.processEvents()
        self.iface.mapCanvas().setMapTool(self.last_maptool)

    def _handle_remove_pt(self):
        """Remove the selected points from the locations table, the last one if none is selected."""
        rows = {idx.row() for idx in self.ui_table.selectionModel().selectedRows()}
        if not rows and self.table_model.rowCount():
            rows.add(self.table_model.rowCount() - 1)
        self.table_model.remove(rows)

        self._reset_annotations()

        # if from context menu, the dialog can be hidden but already have waypoints
        if self.parent_dlg.isHidden():
            self.parent_dlg.show()

    def _reset_annotations(self):
        """Helper method to reset the annotation layer based on the current locations table."""
        # first remove the annotation layer
        kind = self.table_model.table_kind
        if self.points_lyr:
            QgsProject.instance().removeMapLayer(self.points_lyr_id)

        # then build it up again with the new points
        points_lyr = QgsAnnotationLayer(
            kind.ann_layer_name,
            QgsAnnotationLayer.LayerOptions(QgsProject.instance().transformContext()),
        )
        self._ann_ids[kind.name] = points_lyr.id()

        waypoints = self.table_model.waypoints
        for row_id, wp in enumerate(waypoints):
            pt = point_to_wgs84(
                QgsPointXY(wp.lon, wp.lat),
                self.iface.mapCanvas().mapSettings().destinationCrs(),
                QgsCoordinateTransform.TransformDirection.ReverseTransform,
            )
            svg = kind.marker(row_id, len(waypoints), wp)
            points_lyr.addItem(self._get_annotation(QgsPoint(pt.x(), pt.y()), svg))

        # add layer to the project and connect the visibility signal
        QgsProject.instance().addMapLayer(points_lyr)
        self._attach_node_to_slot(kind.name)

    def _attach_node_to_slot(self, kind_name: str):
        """Attaches the node's visibility signal to a slot and sets the checked status of the button"""
        points_node: QgsLayerTreeNode = (
            QgsProject.instance().layerTreeRoot().findLayer(self._ann_ids[kind_name])
        )
        points_node.visibilityChanged.connect(self._handle_points_layer_toggle)
        if kind_name == self.table_model.table_kind.name:
            self.ui_btn_show_point_lyr.setChecked(points_node.isVisible())

    @staticmethod
    def _get_annotation(pt: QgsPoint, svg: str) -> QgsAnnotationMarkerItem:
        """Helper to create an annotation object"""
        symbol = QgsMarkerSymbol()
        symbol.deleteSymbolLayer(0)
        symbol_layer = QgsSvgMarkerSymbolLayer(str(get_resource_path("icons", svg)), 8)
        symbol_layer.setOffsetUnit(QgsUnitTypes.RenderUnit.RenderPoints)
        symbol_layer.setOffset(
            QPointF(0, -11)
        )  # tweaked so the tip of the marker points at the clicked location
        symbol.appendSymbolLayer(symbol_layer)
        annotation = QgsAnnotationMarkerItem(QgsPoint(pt.x(), pt.y()))
        annotation.setSymbol(symbol)

        return annotation

    def _open_persistent_editors(self, first: int = 0, last: int = -1):
        """Keeps the persistent columns' editors open, like cell widgets."""
        last = self.table_model.rowCount() - 1 if last < 0 else last
        for col_id, col in enumerate(self.table_model.table_kind.columns):
            if not col.persistent:
                continue
            for row_id in range(first, last + 1):
                self.ui_table.openPersistentEditor(self.table_model.index(row_id, col_id))

    def _setup_kind_buttons(self):
        """The add button's modes and the imports of the shown kind."""
        kind = self.table_model.table_kind
        self._add_attrs = dict(kind.add_modes[0].attrs) if kind.add_modes else dict()
        if kind.add_modes:
            self._add_menu = QMenu(self)
            for mode in kind.add_modes:
                # the marker the points will get on the map
                icon = get_icon(kind.marker(0, 1, Waypoint(0, 0, dict(mode.attrs))))
                action = self._add_menu.addAction(icon, f"Add {mode.plural}")
                action.triggered.connect(lambda _, a=mode.attrs: self._start_adding(a))
            self.ui_btn_add_pt.setMenu(self._add_menu)
            self.ui_btn_add_pt.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            self.ui_btn_add_pt.setToolTip(
                f"{BUTTONS[WayPtWidgetElems.ADD_PT][1]}; the arrow picks what to add"
            )
        else:
            self.ui_btn_add_pt.setMenu(None)
            self.ui_btn_add_pt.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
            self.ui_btn_add_pt.setToolTip(BUTTONS[WayPtWidgetElems.ADD_PT][1])

        for action in self._routing_import_actions:
            action.setVisible(kind.routing_imports)
        if not kind.routing_imports:
            self.ui_btn_from_lyr.setDefaultAction(self._from_layer_action)

    def setupUi(self):
        """does the same as the usual setupUi() method from pyuic5"""
        self.outer_layout = QVBoxLayout(self)
        self.buttons_layout = QHBoxLayout(self)

        # set up buttons
        self.ui_btn_add_pt = build_btn(self.buttons_layout, WayPtWidgetElems.ADD_PT, True)
        self.ui_btn_rm_pt = build_btn(self.buttons_layout, WayPtWidgetElems.DEL_PT)
        self.ui_btn_rm_all = build_btn(self.buttons_layout, WayPtWidgetElems.DEL_ALL)
        self.ui_btn_from_lyr = build_btn(self.buttons_layout, WayPtWidgetElems.FROM_LYR)
        self.ui_btn_show_point_lyr = build_btn(self.buttons_layout, WayPtWidgetElems.SHOW_POINTS)
        self.ui_btn_show_point_lyr.setCheckable(True)
        self.ui_btn_show_point_lyr.setChecked(True)
        self.ui_btn_up = build_btn(self.buttons_layout, WayPtWidgetElems.MV_UP)
        self.ui_btn_down = build_btn(self.buttons_layout, WayPtWidgetElems.MV_DOWN)

        # do the "from layer/json" dropdown button
        self.ui_btn_from_lyr.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.ui_btn_from_lyr.setAutoRaise(False)
        self.ui_btn_from_lyr.triggered.connect(self.ui_btn_from_lyr.setDefaultAction)

        dropdown_menu = QMenu()
        actions = list()
        for btn, title, connect_fn in (
            (WayPtWidgetElems.FROM_LYR, "Point layer", self._handle_from_layer),
            (
                WayPtWidgetElems.FROM_JSON,
                "Valhalla JSON",
                self._handle_from_valhalla_json,
            ),
            (WayPtWidgetElems.FROM_URL, "OSRM URL", self._handle_from_osrm_url),
        ):
            icon_fp, tooltip = BUTTONS[btn]
            action = QAction(get_icon(icon_fp), f"From {title}", self.parent_dlg)
            action.triggered.connect(connect_fn)
            action.setToolTip(tooltip)
            dropdown_menu.addAction(action)
            actions.append(action)

        self.ui_btn_from_lyr.setMenu(dropdown_menu)
        self.ui_btn_from_lyr.setDefaultAction(actions[0])
        self._from_layer_action = actions[0]
        self._routing_import_actions = actions[1:]

        # add horizontal spacer
        space = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.buttons_layout.insertSpacerItem(5, space)

        # add the buttons to the outer layout
        self.outer_layout.addLayout(self.buttons_layout)

        # add the table view to the outer layout
        self.ui_table = QTableView(self)
        self.ui_table.setModel(self.table_model)
        self.ui_table.setItemDelegate(WaypointDelegate(self.ui_table))
        self.ui_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ui_table.setObjectName(WayPtWidgetElems.TABLE.value)

        # set table dimensions
        self.ui_table.setMinimumHeight(200)
        table_header: QHeaderView = self.ui_table.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.outer_layout.addWidget(self.ui_table)

        # make connections
        self.ui_btn_add_pt.clicked.connect(self._handle_init_maptool)
        self.ui_btn_rm_pt.clicked.connect(self._handle_remove_pt)
        self.ui_btn_rm_all.clicked.connect(self._handle_clear_locations)
        self.point_tool.canvasClicked.connect(self._handle_add_pt)
        self.point_tool.doubleClicked.connect(self._handle_doubleclick)
        self.ui_btn_up.clicked.connect(self._handle_pt_up)
        self.ui_btn_down.clicked.connect(self._handle_pt_down)
        self.ui_btn_show_point_lyr.clicked.connect(self._handle_points_btn_toggle)
