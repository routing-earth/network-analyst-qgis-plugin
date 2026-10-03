import json

from qgis.core import Qgis, QgsMapLayerProxyModel
from qgis.gui import QgisInterface
from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QTextEdit, QWidget

from ...global_definitions import SETTINGS_WIDGETS_MAP, RouterProfile
from ...utils.layer_utils import get_wgs_coords_from_layer
from ...utils.misc_utils import deep_merge
from .. import UI_RESOURCE_PATH

GENERATED_FORM_CLASS, _ = uic.loadUiType(str(UI_RESOURCE_PATH / "routing_params_widget.ui"))


class RoutingParamsWidget(QWidget, GENERATED_FORM_CLASS):
    def __init__(
        self,
        parent_dlg: QWidget = None,
        iface: QgisInterface = None,
    ):
        """
        Represents the routing parameter widget. Needs to be added to the parent's layout
        widget by the caller.

        :param parent_dlg: Parent dialog
        :param iface: The QGIS interface
        """
        super().__init__(parent_dlg)
        self.parent_dlg = parent_dlg
        self.iface = iface
        self.setupUi(self)

        # set defaults of exclude location layers to empty layer and disallow certain GeometryTypes
        self.exclude_locations.setAllowEmptyLayer(True)
        self.exclude_polygons.setAllowEmptyLayer(True)
        self.exclude_locations.setCurrentIndex(0)
        self.exclude_polygons.setCurrentIndex(0)
        self.exclude_locations.setFilters(QgsMapLayerProxyModel.Filter.PointLayer)
        self.exclude_polygons.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)

        # connections
        self.ui_reset_settings.clicked.connect(self._on_settings_reset)

        # add profile setting widgets
        for router_profile in RouterProfile:
            setattr(
                self,
                SETTINGS_WIDGETS_MAP[router_profile]["ui_name"],
                SETTINGS_WIDGETS_MAP[router_profile]["widget"](self),
            )
            self.ui_settings_stacked.addWidget(
                getattr(self, SETTINGS_WIDGETS_MAP[router_profile]["ui_name"])
            )

    def _on_settings_reset(self):

        old_widget: QWidget = self.ui_settings_stacked.currentWidget()
        # get the current profile
        profile = next(
            profile
            for profile, widget in SETTINGS_WIDGETS_MAP.items()
            if isinstance(old_widget, widget["widget"])
        )

        # remove old widget
        index = self.ui_settings_stacked.currentIndex()
        self.ui_settings_stacked.removeWidget(old_widget)
        old_widget.setParent(None)

        # create new one at old index: we need the currently selected profile from the parent_dlg
        setattr(
            self,
            SETTINGS_WIDGETS_MAP[profile]["ui_name"],
            SETTINGS_WIDGETS_MAP[profile]["widget"](self),
        )
        new_widget = getattr(self, SETTINGS_WIDGETS_MAP[profile]["ui_name"])
        self.ui_settings_stacked.insertWidget(index, new_widget)

        # currentIndex increments when new widget is inserted before or at currentIndex, so we need to manually reset it
        self.ui_settings_stacked.setCurrentWidget(new_widget)

        # finally, also reset global costing options
        self.exclude_locations.setCurrentIndex(0)
        self.exclude_polygons.setCurrentIndex(0)
        self.ui_metric_fastest.setChecked(True)

    def set_current_costing_widget(self, profile: RouterProfile):
        self.ui_settings_stacked.setCurrentWidget(
            getattr(self, SETTINGS_WIDGETS_MAP[profile]["ui_name"])
        )

    def get_costing_params(self):  # noqa: C901
        params = dict()
        exclude_locations_lyr = self.exclude_locations.currentLayer()

        # time settings
        if self.ui_time_box.isChecked() and not self.ui_date_time_value.isNull():
            dt: str = self.ui_date_time_value.dateTime().toString("yyyy-MM-ddTHH:mm")
            if self.ui_time_depart.isChecked():
                dt_type = 1
            elif self.ui_time_arrive.isChecked():
                dt_type = 2
            else:
                dt_type = 0

            params["date_time"] = {"value": dt, "type": dt_type}

        if exclude_locations_lyr:
            params["exclude_locations"] = get_wgs_coords_from_layer(exclude_locations_lyr)

        exclude_polygons_lyr = self.exclude_polygons.currentLayer()

        if exclude_polygons_lyr:
            params["exclude_polygons"] = get_wgs_coords_from_layer(exclude_polygons_lyr)

        params["options"] = self.ui_settings_stacked.currentWidget().get_params()
        if self.ui_metric_shortest.isChecked():
            params["options"]["shortest"] = True

        # get the extra JSON params if specified
        self.ui_extra_json: QTextEdit
        if self.ui_extra_box.isChecked() and (json_text := self.ui_extra_json.toPlainText()):
            try:
                js: dict = json.loads(json_text)
                params = deep_merge(params, js)
            except json.JSONDecodeError as e:
                self.parent_dlg.status_bar.pushMessage(
                    "Invalid extra JSON parameters", str(e), Qgis.MessageLevel.Critical, 8
                )

        return params
