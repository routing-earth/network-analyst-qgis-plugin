import json
import webbrowser
from copy import deepcopy
from typing import List, Optional, Tuple

from osgeo import gdal
from qgis.core import (  # noqa: F811
    Qgis,
    QgsFeature,
    QgsFields,
    QgsGeometry,
    QgsMapLayer,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgisInterface, QgsDockWidget
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QProcess, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from qgis.PyQt.QtWidgets import (
    QAction,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QTextEdit,
    QToolButton,
    QWidget,
)

from .. import PLUGIN_NAME
from ..core import graph_registry
from ..core.results_factory import ResultsFactory
from ..core.settings import (
    DEFAULT_PROVIDERS,
    ProviderSetting,
    ValhallaSettings,
)
from ..exceptions import ValhallaError
from ..global_definitions import (
    DEFAULT_LAYER_FIELDS,
    Dialogs,
    RouterEndpoint,
    RouterType,
)
from ..third_party.routingpy import routingpy
from ..third_party.routingpy.routingpy.utils import deep_merge_dicts
from ..utils.http_utils import get_status_response
from ..utils.layer_utils import post_process_layer
from ..utils.resource_utils import (
    create_valhalla_config,
    get_default_valhalla_binary_dir,
    get_icon,
    get_resource_path,
)
from . import UI_RESOURCE_PATH
from .dlg_about import AboutDialog
from .gui_utils import add_msg_bar
from .widgets.widget_router import PROFILE_TO_UI, RouterWidget
from .widgets.widget_routing_params import RoutingParamsWidget
from .widgets.widget_waypoints import WaypointsWidget

GENERATED_FORM_CLASS, _ = uic.loadUiType(str(UI_RESOURCE_PATH / "widget_routing_dock.ui"))

MENU_TABS = {
    RouterEndpoint.DIRECTIONS: "ui_directions_params",
    RouterEndpoint.TSP: "ui_tsp_params",
    RouterEndpoint.ISOCHRONES: "ui_isochrones_params",
    RouterEndpoint.MATRIX: "ui_matrix_params",
    RouterEndpoint.MAP_MATCH: "ui_map_matching_params",
    RouterEndpoint.EXPANSION: "ui_expansion_params",
    RouterEndpoint.ELEVATION: "ui_elevation_params",
}

HELP_URL = "https://github.com/nilsnolde/valhalla-qgis-plugin?tab=readme-ov-file#how-to"


class RoutingDockWidget(QgsDockWidget, GENERATED_FORM_CLASS):
    def __init__(self, iface: QgisInterface = None):
        QgsDockWidget.__init__(self)
        widget = QWidget(self)
        self.setupUi(widget)
        self.ui_log_btn.setIcon(get_icon("url.svg"))
        self.ui_graph_btn.setIcon(get_icon("graph_extent_icon.svg"))
        # routing.earth logo button — white variant on dark themes for contrast
        is_dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        self.ui_re_btn.setIcon(get_icon("re_logo_dark.png" if is_dark else "re_logo.png"))

        self.iface = iface

        # add a status bar
        self.status_bar = add_msg_bar(self.verticalWrapper)
        # keep the params so we can show them in the log window
        self.log_params = dict()
        self.endpoint = ""

        # make sure we have some default settings:
        # - at least one remote HTTP API URL and localhost
        # - the default binary path
        # - the graph registry (+ one-shot legacy migration)
        # - a valhalla.json we can overwrite with the current graph settings
        settings = ValhallaSettings()
        if not settings.get_providers(RouterType.VALHALLA):
            for prov in DEFAULT_PROVIDERS:
                settings.set_provider(RouterType.VALHALLA.lower(), prov)
        if not settings.get_binary_dir():
            bin_dir = get_default_valhalla_binary_dir()
            bin_dir.parent.parent.mkdir(exist_ok=True, parents=True)
            settings.set_binary_dir(bin_dir)

        # ensure the graph library dir exists (sole source of truth for graphs)
        graph_registry.graph_dir()

        # add custom widgets to this dialog
        self.router_widget = RouterWidget(self)
        self.router_box.layout().addWidget(self.router_widget)
        self.waypoints_widget = WaypointsWidget(self, self.iface)
        self.waypoints_box.layout().addWidget(self.waypoints_widget)
        self.routing_params_widget = RoutingParamsWidget(self, self.iface)
        self.options_box.layout().addWidget(self.routing_params_widget)

        # initial factory, will/should always be HTTP
        self.factory = ResultsFactory(
            self.router_widget.router,
            self.router_widget.method,
            self.router_widget.profile,
            self.router_widget.provider.url,
        )

        self._on_profile_change()

        # "on air" blinker: while the local valhalla server runs, the stop
        # button's black-square icon pulses red at a fixed cadence
        self._blink_on = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(750)
        self._blink_timer.timeout.connect(self._on_server_blink)
        # no valhalla_service on Windows (no local server support)
        if hasattr(self.router_widget, "valhalla_service"):
            self.router_widget.valhalla_service.stateChanged.connect(self._on_server_state_changed)

        # connections
        self.menu_widget.currentRowChanged["int"].connect(self._on_menu_change)
        self.menu_widget.currentRowChanged["int"].connect(self.ui_params_stacked.setCurrentIndex)
        self.router_widget.ui_cmb_prov.currentIndexChanged.connect(self._on_provider_changed)
        self.execute_btn.clicked.connect(self._on_execute)
        self.router_widget.ui_btn_server_info.clicked.connect(self._on_about_click)
        self.ui_log_btn.clicked.connect(self._on_log_click)
        self.router_widget.mode_btns.buttonToggled.connect(self._on_profile_change)
        self.ui_graph_btn.clicked.connect(self._on_graph_click)
        self.ui_help_btn.clicked.connect(lambda: webbrowser.open(HELP_URL))
        self.ui_re_btn.clicked.connect(
            lambda: webbrowser.open("https://routing-earth.com/software/qgis-plugin")
        )

        # icons on left side menu
        self.menu_widget: QListWidget
        self.menu_widget.item(0).setIcon(get_icon("directions_icon.svg"))
        self.menu_widget.item(1).setIcon(get_icon("optimized_directions_icon.svg"))
        self.menu_widget.item(2).setIcon(get_icon("isochrones_icon.svg"))
        self.menu_widget.item(3).setIcon(get_icon("matrix_icon.svg"))
        self.menu_widget.item(4).setIcon(get_icon("trace_route_icon.svg"))
        self.menu_widget.item(5).setIcon(get_icon("expansion_icon.svg"))
        self.menu_widget.item(6).setIcon(get_icon("height_icon.svg"))
        self.menu_widget.setCurrentRow(0)

        self.setWindowTitle(f"{PLUGIN_NAME} - Routing")

        self.setWidget(widget)

        try:
            create_valhalla_config()
        except ModuleNotFoundError:
            pass

        # add a context menu to the canvas
        self.iface.mapCanvas().contextMenuAboutToShow.connect(self._populate_canvas_menu)

    def _get_params(self, endpoint: RouterEndpoint) -> dict:  # noqa: C901
        """Returns the current parameters"""

        def get_intervals(widget: QLineEdit) -> List[float]:
            try:
                intervals = [float(x) for x in widget.text().split(",")]
                if not intervals:
                    raise ValueError
                return intervals
            except ValueError:
                return list()

        params = dict()
        if self.router_widget.router == RouterType.VALHALLA:
            if endpoint in (RouterEndpoint.DIRECTIONS, RouterEndpoint.TSP):
                params["instructions"] = False

            elif endpoint == RouterEndpoint.ISOCHRONES:
                params["intervals"] = get_intervals(self.ui_isochrone_intervals)
                params["interval_type"] = (
                    "distance" if self.ui_isochrone_distance.isChecked() else "time"
                )
                params["polygons"] = True
                params["denoise"] = float(self.ui_isochrone_denoise.value())
                params["generalize"] = int(self.ui_isochrone_generalize.value())
                if self.ui_isochrone_format.currentText() == "geotiff":
                    params["format"] = "geotiff"

            elif endpoint == RouterEndpoint.EXPANSION:
                params["intervals"] = get_intervals(self.ui_expansion_intervals)
                params["interval_type"] = (
                    "distance" if self.ui_expansion_distance.isChecked() else "time"
                )
                params["expansion_properties"] = ("duration", "distance")
                params["dedupe"] = self.ui_expansion_dedupe.isChecked()
                params["skip_opposites"] = self.ui_expansion_skip_opps.isChecked()

            elif endpoint == RouterEndpoint.MAP_MATCH:
                params["shape_match"] = "map_snap"
                params["narrative"] = False

                trace_options = dict()
                if v := int(self.ui_valhalla_mapmatch_search_radius.value()):
                    trace_options["search_radius"] = v
                if v := int(self.ui_valhalla_mapmatch_gps_accuracy.value()):
                    trace_options["gps_accuracy"] = v
                if v := int(self.ui_valhalla_mapmatch_breakage_distance.value()):
                    trace_options["breakage_distance"] = v

                print(trace_options)

                if len(trace_options):
                    params["trace_options"] = trace_options

            # only append the costing options if the costing options widget is active
            return {
                **params,
                **(
                    self.routing_params_widget.get_costing_params()
                    if self.options_box.isChecked() and endpoint != RouterEndpoint.ELEVATION
                    else dict()
                ),
            }
        else:
            if endpoint == RouterEndpoint.DIRECTIONS:
                params["overview"] = "full"

            return params

    def _get_output_layer(
        self,
        endpoint: RouterEndpoint,
        locations: List[Tuple[float, float]],
        params: dict,
    ) -> QgsMapLayer:
        """
        Returns the result features as a single vector layer.

        :endpoint: one of RouterEndpoint
        :profile: one of RouterProfile
        :locations: locations as iterable of lng/lat coordinate tuples
        :params: additional parameter dictionary
        """
        router = self.router_widget.router
        layer_name = "{} {} {}".format(
            router.capitalize() if router == RouterType.VALHALLA else router.upper(),
            endpoint.capitalize(),
            (
                self.router_widget.profile.capitalize()
                if self.router_widget.profile and endpoint != RouterEndpoint.ELEVATION
                else ""
            ),
        )

        if params.get("format") == "geotiff":
            vsipath = f'/vsimem/{layer_name.replace(" ", "_")}.tif'
            gdal.FileFromMemBuffer(
                vsipath, next(self.factory.get_results(RouterEndpoint.RASTER, locations, params))
            )
            out_lyr = QgsRasterLayer(vsipath, layer_name)
        else:
            out_lyr = QgsVectorLayer(
                f"{QgsWkbTypes.displayString(self.factory.geom_type(endpoint))}?crs=EPSG:4326",
                layer_name,
                "memory",
            )
            layer_fields = QgsFields()
            for f in DEFAULT_LAYER_FIELDS[endpoint]:
                layer_fields.append(f)

            out_lyr.dataProvider().addAttributes(layer_fields)
            out_lyr.updateFields()

            for feat in self.factory.get_results(endpoint, locations, params):
                out_lyr.dataProvider().addFeature(feat)

            out_lyr.updateExtents()

            # give the layer some styling etc.
            post_process_layer(out_lyr, endpoint)

        return out_lyr

    def _on_execute(self):
        self.endpoint = list(MENU_TABS)[self.menu_widget.currentRow()]
        params = self._get_params(self.endpoint)
        self.factory.profile = self.router_widget.profile  # update profile

        if (
            self.endpoint in (RouterEndpoint.ISOCHRONES, RouterEndpoint.EXPANSION)
            and not params["intervals"]
        ):
            self.status_bar.pushMessage(
                "Please provide intervals as comma separated numbers (e.g. 100,200,300)",
                Qgis.MessageLevel.Critical,
                8,
            )
            return

        # get both the locations and location parameters (only OSRM) from the waypoint table
        locations = self.waypoints_widget.get_locations(self.router_widget.router)
        params.update(self.waypoints_widget.get_extra_params(self.router_widget.router))

        # build the stuff for logging
        # params have "options" instead of "costing_options" bcs of routing-py
        self.log_params = deepcopy(params)
        if current_options := self.log_params.get("options"):
            self.log_params.pop("options")
            temp = {"costing_options": {self.router_widget.profile: current_options}}
            self.log_params = deep_merge_dicts(temp, self.log_params)

        self.log_params["costing"] = self.router_widget.profile
        self.log_params["locations"] = [
            {"lon": wp._position[0], "lat": wp._position[1], **wp._kwargs} for wp in locations
        ]

        try:
            lyr = self._get_output_layer(self.endpoint, locations, params)
        except routingpy.exceptions.RouterError as e:  # HTTP error
            msg = str(e.message.get("error") or e.message) if isinstance(e.message, dict) else e.message
            self.status_bar.pushMessage(
                f"HTTP Error {e.status}",
                msg,
                Qgis.MessageLevel.Critical,
                8,
            )
            return
        except routingpy.exceptions.JSONParseError as e:
            self.status_bar.pushMessage("Invalid response", str(e), Qgis.MessageLevel.Critical, 8)
            return
        except (RuntimeError, ValhallaError) as e:  # Bindings & factory error
            self.status_bar.pushMessage("Error", str(e), Qgis.MessageLevel.Critical, 8)
            return

        QgsProject.instance().addMapLayer(lyr)

    def _on_settings_change(self, new_text, widget: Optional[QWidget] = ""):
        attr = widget.objectName() if widget else self.sender().objectName()
        ValhallaSettings().set(Dialogs.ROUTING, attr, str(new_text))

    def _on_menu_change(self, menu_index: int):
        """Only changes the window title"""

        title = f"{PLUGIN_NAME} - "
        if menu_index == 0:
            title += "Routing"
        elif menu_index == 1:
            title += "Traveling Salesman"
        elif menu_index == 2:
            title += "Isochrones"
        elif menu_index == 3:
            title += "Matrix"
        elif menu_index == 4:
            title += "Map Match"
        elif menu_index == 5:
            title += "Expansion"
        elif menu_index == 6:
            title += "Elevation"
        else:
            raise ValueError(f"Need to update menu index {menu_index}")

        self.setWindowTitle(title)

    def _on_server_state_changed(self, new_state: QProcess.ProcessState):
        running = new_state != QProcess.ProcessState.NotRunning
        if running:
            self._blink_timer.start()
        else:
            self._blink_timer.stop()
            self._blink_on = False
            # clear the red tint back to the run icon (router widget owns the
            # button; both handlers fire on stateChanged, order not guaranteed)
            self.router_widget.ui_btn_server_start.setIcon(
                get_icon(":images/themes/default/mActionStart.svg")
            )

    def _on_server_blink(self):
        """The "on air" blink: pulse the stop button's black square icon red."""
        self._blink_on = not self._blink_on
        icon = (
            self._red_stop_icon()
            if self._blink_on
            else get_icon(":images/themes/default/mActionStop.svg")
        )
        self.router_widget.ui_btn_server_start.setIcon(icon)

    def _red_stop_icon(self) -> QIcon:
        """A red-tinted copy of the stop icon (cached), matching the button size."""
        if getattr(self, "_red_stop_icon_cache", None) is None:
            btn = self.router_widget.ui_btn_server_start
            size = btn.iconSize()
            src = get_icon(":images/themes/default/mActionStop.svg").pixmap(size)
            tinted = QPixmap(src.size())
            tinted.fill(Qt.GlobalColor.transparent)
            painter = QPainter(tinted)
            painter.drawPixmap(0, 0, src)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(tinted.rect(), QColor("#ff2222"))
            painter.end()
            self._red_stop_icon_cache = QIcon(tinted)
        return self._red_stop_icon_cache

    def _on_about_click(self):
        about = AboutDialog(self)
        if exc_msg := about.exception_msg:
            self.status_bar.pushWarning("Failed /status request", exc_msg)
        about.exec()

    def _on_log_click(self):
        if not self.factory.url or not self.log_params:
            self.status_bar.pushMessage("Nothing happened yet:)", Qgis.MessageLevel.Warning, 3)
            return

        msg_box = QMessageBox()
        msg_box.setWindowTitle(f"Last {self.endpoint.lower()} request")
        msg_box.setText(self.factory.url)
        msg_box.setDetailedText(json.dumps(self.log_params, indent=2))
        edit_box = msg_box.findChild(QTextEdit)
        edit_box.setFixedHeight(edit_box.height() + 300)

        # show the details (i.e. request params) by default
        for btn in msg_box.buttons():
            if msg_box.buttonRole(btn) == QMessageBox.ButtonRole.ActionRole:
                btn.click()

        msg_box.exec()

    def _on_provider_changed(self, row_id: int):
        """
        Enables/disables certain UI elements depending on the provider.
        """
        # disables/enables other widgets in this dialog
        self._on_menu_change(self.menu_widget.currentRow())

        router_type = self.router_widget.router
        method = self.router_widget.method
        profile = self.router_widget.profile
        pkg_path = ""
        provider: ProviderSetting = self.router_widget.provider

        # reset the factory
        self.factory = ResultsFactory(router_type, method, profile, pkg_path=pkg_path, url=provider.url)

    def _on_graph_click(self):
        curr_prov: ProviderSetting = self.router_widget.ui_cmb_prov.currentData()[-1]
        if curr_prov.url == DEFAULT_PROVIDERS[0].url:
            self.status_bar.pushInfo(
                "No worries", "The public server has the full world graph and admins/timezones loaded"
            )
            return

        try:
            result = get_status_response(self.router_widget.provider.url, True)
            if not result.get("bbox"):
                raise Exception(
                    "Can't retrieve graph extent, the server doesn't allow it, see https://valhalla.github.io/valhalla/api/status/api-reference/."
                )
        except Exception as e:
            self.status_bar.pushCritical("Failed /status request", str(e))
            return

        msg_missing_dbs: List[str] = []
        if not result["has_admins"]:
            msg_missing_dbs.append("Admin DB is missing from graph, expect wrong driving side.")
        if not result["has_timezones"]:
            msg_missing_dbs.append(
                "Timezones DB is missing from graph, expect non-functional time-dependent settings."
            )
        if msg_missing_dbs:
            self.status_bar.pushWarning("Missing DB(s)", "\n".join(msg_missing_dbs))
        else:
            self.status_bar.pushInfo(
                "DB(s) exist", "Both admin areas and timezones are built into the graph."
            )

        # add the graph extent layer
        lyr_name = f"{curr_prov.name} - Valhalla Graph Extent"
        if QgsProject.instance().mapLayersByName(lyr_name):
            self.status_bar.pushInfo(
                "Layer exists", f"Graph extents layer '{lyr_name}' already exists, skipping..."
            )
            return

        extent_lyr = QgsVectorLayer("Polygon?crs=EPSG:4326", lyr_name, "memory")
        feat = QgsFeature(QgsFields())
        feat.setGeometry(
            QgsGeometry.fromPolygonXY(
                [
                    [
                        QgsPointXY(lon, lat)
                        for lon, lat in result["bbox"]["features"][0]["geometry"]["coordinates"][0]
                    ]
                ]
            )
        )
        extent_lyr.dataProvider().addFeature(feat)
        extent_lyr.updateExtents()
        extent_lyr.loadNamedStyle(str(get_resource_path("styles", "graph_extent.qml")))
        QgsProject.instance().addMapLayer(extent_lyr, True)

    def _on_profile_change(self):
        for ui_enum, profile_enum in PROFILE_TO_UI.items():
            btn: QToolButton = getattr(self.router_widget, ui_enum.value)
            if btn.isChecked():
                self.routing_params_widget.set_current_costing_widget(profile_enum)
                return

    def _populate_canvas_menu(self, menu: QMenu, event):
        """Executed on every right-click in the map canvas"""
        map_pt = event.mapPoint()

        routing_menu = menu.addMenu(PLUGIN_NAME)
        routing_menu.setIcon(get_icon("valhalla_logo.svg"))

        origin = QAction(get_icon("origin.svg"), "Add waypoint", menu)
        origin.triggered.connect(lambda: self.waypoints_widget._handle_add_pt(map_pt))
        routing_menu.addAction(origin)

        routing_menu.addSeparator()

        # clear waypoints
        rm_pt = QAction(
            get_icon(":images/themes/default/symbologyRemove.svg"), "Clear last waypoint", menu
        )
        clear_pts = QAction(
            get_icon(":images/themes/default/mActionRemove.svg"), "Clear all waypoints", menu
        )

        rm_pt.triggered.connect(self.waypoints_widget._handle_remove_pt)
        clear_pts.triggered.connect(self.waypoints_widget._handle_clear_locations)

        routing_menu.addAction(rm_pt)
        routing_menu.addAction(clear_pts)

    def unload(self):
        # don't leave an orphaned valhalla server behind on plugin
        # reload/unload or QGIS shutdown (no valhalla_service on Windows);
        # wait briefly so the port is free again for a reloaded instance
        service = getattr(self.router_widget, "valhalla_service", None)
        if service is not None and service.state() != QProcess.ProcessState.NotRunning:
            self.router_widget._on_server_stop()
            service.waitForFinished(2000)

        # kill any in-flight RE / graph-build subprocess so a reload doesn't
        # leave a lingering QProcess whose stale state reads as "Busy"
        graph_widget = getattr(getattr(self.router_widget, "settings_dlg", None), "graph_widget", None)
        if graph_widget is not None:
            graph_widget.shutdown()

        try:
            self.iface.mapCanvas().contextMenuAboutToShow.disconnect(self._populate_canvas_menu)
        except TypeError:
            pass
