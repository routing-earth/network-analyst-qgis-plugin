import json
import platform

from qgis.core import Qgis
from qgis.PyQt.QtCore import QEvent, QFileSystemWatcher, QProcess, QSize
from qgis.PyQt.QtWidgets import (
    QAction,
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QWidget,
)

from ...core import graph_registry
from ...core.settings import ProviderSetting, ValhallaSettings
from ...global_definitions import RouterMethod, RouterProfile, RouterType
from ...gui.dlg_plugin_settings import PluginSettingsDialog
from ...gui.dlg_routing_providers import ProviderDialog
from ...gui.dlg_server_log import ServerLogDialog
from ...utils.misc_utils import deep_merge
from ...utils.resource_utils import (
    check_valhalla_installation,
    create_valhalla_config,
    get_icon,
    get_valhalla_config_path,
)
from ..ui_definitions import ID_JSON, RouterWidgetElems

PROFILE_TO_UI = {
    RouterWidgetElems.PED: RouterProfile.PED,
    RouterWidgetElems.BIKE: RouterProfile.BIKE,
    RouterWidgetElems.CAR: RouterProfile.CAR,
    RouterWidgetElems.TRUCK: RouterProfile.TRUCK,
    RouterWidgetElems.MBIKE: RouterProfile.MBIKE,
    RouterWidgetElems.BUS: RouterProfile.BUS,
}


# TODO: make this class a singleton so every dialog gets the same instance
#   mainly useful for the QFileSystemWatcher
class RouterWidget(QWidget):
    def __init__(self, parent_dlg: QWidget = None):
        super().__init__()
        self._parent = parent_dlg
        self.setupUi()

        self.settings_dlg = PluginSettingsDialog(self)

        self._populate_providers()
        self._on_graph_changed(self.ui_cmb_graphs.currentText())

        # assign and update the provider & method
        self._on_provider_method_changed()
        self._profile = RouterProfile.PED

        # connections
        self.ui_cmb_prov.currentIndexChanged.connect(self._on_provider_method_changed)
        self.mode_btns.buttonToggled.connect(self._on_profile_change)
        self.ui_btn_prov_options.clicked.connect(self._on_btn_prov_options_clicked)

        # TODO: https://github.com/kevinkreiser/prime_server/pull/137
        # Windows has no service support yet, so no need to enable local servers
        if platform.system() == "Windows":
            self.ui_btn_server_start.setEnabled(False)
            self.ui_btn_server_log.setEnabled(False)
            self.ui_btn_server_conf.setEnabled(False)
            self.ui_cmb_graphs.setEnabled(False)
            # local server unsupported: only the info action is meaningful, so
            # make it the default (clicked) action of the menu button
            self.ui_btn_server_menu.setDefaultAction(self.ui_btn_server_info)
            return

        # below ONLY for linux/osx

        # the process which will start a local valhalla server
        self.valhalla_service = QProcess(self)
        self.valhalla_service.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.dlg_server_log = ServerLogDialog()

        # watch the graph library and rebuild the combobox items on any change.
        # the watcher only fires on DIRECT children of graph_dir, so it misses a
        # newly registered graph whose id.json is written INSIDE <name>/ (e.g. an
        # RE download) — re-scan on dropdown open so selection is always current.
        self.graph_dir_watcher = QFileSystemWatcher([str(graph_registry.graph_dir())], self)
        self.graph_dir_watcher.directoryChanged.connect(self._refresh_graph_combo)
        self.ui_cmb_graphs.installEventFilter(self)
        self._refresh_graph_combo()

        # more connections
        self.ui_btn_server_conf.clicked.connect(self._on_settings_clicked)
        self.ui_btn_server_start.clicked.connect(self._on_server_toggle)
        self.ui_btn_server_log.triggered.connect(self.dlg_server_log.show)
        self.ui_cmb_graphs.currentTextChanged.connect(self._on_graph_changed)
        self.valhalla_service.readyReadStandardOutput.connect(self._on_server_log_ready)
        self.valhalla_service.stateChanged.connect(self._on_server_state_changed)

    def eventFilter(self, obj, event):
        # rescan right before the graph dropdown opens (see watcher note above)
        if obj is self.ui_cmb_graphs and event.type() == QEvent.Type.MouseButtonPress:
            self._refresh_graph_combo()
        return super().eventFilter(obj, event)

    def _refresh_graph_combo(self, _path: str = ""):
        current = self.ui_cmb_graphs.currentText()
        items = graph_registry.list_names()
        self.ui_cmb_graphs.blockSignals(True)
        self.ui_cmb_graphs.clear()
        self.ui_cmb_graphs.addItems(items)
        if current in items:
            self.ui_cmb_graphs.setCurrentText(current)
        self.ui_cmb_graphs.blockSignals(False)

    @property
    def router(self) -> RouterType:
        return self._router

    @property
    def provider(self) -> ProviderSetting:
        return self._provider

    @property
    def method(self) -> RouterMethod:
        return self._method

    @property
    def package_path(self) -> str:
        # only relevant for RouterMethod.LOCAL, which is currently never
        # registered — kept for dlg_spopt's LOCAL branch
        return self._package_path

    @property
    def profile(self) -> RouterProfile:
        return self._profile

    @profile.setter
    def profile(self, profile):
        self._profile = profile

    def _on_graph_changed(self, new_name):
        if not new_name:
            return

        # load the current graph settings (tile_dir etc)
        id_json = graph_registry.graph_dir().joinpath(new_name, ID_JSON).resolve()
        try:
            with id_json.open("r") as f:
                graph_settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            # the entry vanished/broke while selected
            self._parent.status_bar.pushMessage(
                f"Graph '{new_name}' is not registered (anymore)", Qgis.MessageLevel.Warning, 6
            )
            return

        # the routing_earth block is plugin metadata, not valhalla config
        graph_settings.pop("routing_earth", None)

        # overwrite valhalla.json with those graph settings
        config = get_valhalla_config_path()
        if not config.exists():
            return

        with config.open("r+") as f:
            valhalla_settings = json.load(f)
            new_settings = deep_merge(valhalla_settings, graph_settings)
            f.seek(0)
            f.truncate()
            json.dump(new_settings, f, indent=2)

    def _on_server_toggle(self):
        if self.valhalla_service.state() == QProcess.ProcessState.NotRunning:
            self._on_server_start()
        else:
            self._on_server_stop()

    def _on_server_state_changed(self, new_state: QProcess.ProcessState):
        # the one start/stop button follows the process state
        if new_state == QProcess.ProcessState.NotRunning:
            self.ui_btn_server_start.setIcon(get_icon(":images/themes/default/mActionStart.svg"))
            self.ui_btn_server_start.setToolTip("Start a local Valhalla server")
        else:  # Starting or Running
            self.ui_btn_server_start.setIcon(get_icon(":images/themes/default/mActionStop.svg"))
            self.ui_btn_server_start.setToolTip("Stop the local Valhalla server")

    def _on_server_log_ready(self):
        log = self.valhalla_service.readAll().data().decode()
        self.dlg_server_log.text_log.append(log)

    def _on_server_start(self):
        binary_dir = ValhallaSettings().get_binary_dir()
        no_binary_dir = False
        msg = ""
        if not check_valhalla_installation():
            no_binary_dir = True
            msg += "pyvalhalla is not installed."
        elif self.ui_cmb_graphs.currentIndex() == -1:
            msg += "No graph selected."
            no_binary_dir = True

        if no_binary_dir:
            self._parent.status_bar.pushMessage(msg, Qgis.MessageLevel.Critical, 6)
            self.settings_dlg.open()
            return

        # at this point the valhalla.json might not exist yet
        try:
            create_valhalla_config()
        except ModuleNotFoundError as e:
            self._parent.status_bar.pushMessage(e.msg, Qgis.MessageLevel.Critical, 6)
            return

        # re-merge the selected graph's id.json EVERY start: the entry may
        # have been replaced/synced since the combo selection last changed,
        # and valhalla.json would otherwise keep serving stale paths
        self._on_graph_changed(self.ui_cmb_graphs.currentText())

        args = [str(get_valhalla_config_path()), "1"]

        # need to run the executable directly
        # with "python -m valhalla xxx" it'd run 2 processes and only kill the first/outer one
        valhalla_service = binary_dir.joinpath("valhalla_service")
        self.valhalla_service.start(str(valhalla_service.resolve()), args)
        self.dlg_server_log.text_log.append(
            f"Started {valhalla_service} with PID {self.valhalla_service.processId()}..."
        )

    def _on_server_stop(self):
        if self.valhalla_service.state() == QProcess.ProcessState.NotRunning:
            return
        self.dlg_server_log.text_log.append("Stopping valhalla service...")
        self.valhalla_service.kill()

    def _on_settings_clicked(self):
        self.settings_dlg.show()

    def _on_provider_method_changed(self):
        if self.ui_cmb_prov.currentIndex() == -1:
            return

        (
            self._router,
            self._method,
            self._package_path,
            self._provider,
        ) = self.ui_cmb_prov.currentData()

    def _on_profile_change(self):
        self._profile = PROFILE_TO_UI[RouterWidgetElems(self.mode_btns.checkedButton().objectName())]

    def _populate_providers(self):
        """Fill the provider's combobox"""
        # need to block signals here, another function also connects to the
        # combobox's signal and would be triggered otherwise
        self.ui_cmb_prov.blockSignals(True)

        prev_idx = self.ui_cmb_prov.currentIndex()
        self.ui_cmb_prov.clear()

        # first add the remote options
        for provider in ValhallaSettings().get_providers(RouterType.VALHALLA):
            self.ui_cmb_prov.addItem(
                provider.name, (RouterType.VALHALLA, RouterMethod.REMOTE, "", provider)
            )

        # jump to previous combobox index if it's all valid
        if prev_idx != -1 and self.ui_cmb_prov.count() >= (prev_idx + 1):
            self.ui_cmb_prov.setCurrentIndex(prev_idx)

        self.ui_cmb_prov.blockSignals(False)

    def setupUi(self):
        def add_btn(btn_name: str, icon: str, tip: str, checkable=True) -> QToolButton:
            btn = QToolButton(self)
            btn.setIcon(get_icon(icon))
            btn.setObjectName(btn_name)
            btn.setIconSize(QSize(24, 24))
            btn.setToolTip(tip)
            btn.setCheckable(checkable)
            setattr(self, btn_name, btn)

            return btn

        self.outer_layout = QFormLayout(self)

        # the upper row, i.e. providers
        self.provider_field = QHBoxLayout(self)
        self.ui_cmb_prov = QComboBox(self)
        self.ui_cmb_prov.setObjectName(RouterWidgetElems.PROV_COMBO.value)
        add_btn(RouterWidgetElems.PROV_OPT.value, "server.svg", "Server manager", False)

        self.provider_field.addWidget(self.ui_cmb_prov)
        self.provider_field.addWidget(self.ui_btn_prov_options)

        self.outer_layout.addRow("Provider", self.provider_field)

        # the middle row, i.e. local server; one button toggles start/stop
        self.server_layout = QHBoxLayout(self)
        self.server_layout.addWidget(
            add_btn(
                RouterWidgetElems.SERVER_START.value,
                ":images/themes/default/mActionStart.svg",
                "Start a local Valhalla server",
                False,
            )
        )
        # settings as a normal tool button, between start/stop and the combo
        self.server_layout.addWidget(
            add_btn(
                RouterWidgetElems.SERVER_CONF.value,
                ":images/themes/default/propertyicons/layerconfiguration.svg",
                "Configure the local server",
                False,
            )
        )
        self.ui_cmb_graphs = QComboBox(self)
        self.ui_cmb_graphs.setObjectName(RouterWidgetElems.SERVER_GRAPHS_COMBO.value)
        self.ui_cmb_graphs.setToolTip("List of locally available graphs")
        self.server_layout.addWidget(self.ui_cmb_graphs)

        # a tool button menu to save space
        self.ui_btn_server_menu = QToolButton(self)
        self.ui_btn_server_menu.setObjectName("ui_btn_server_menu")
        self.ui_btn_server_menu.setIconSize(QSize(24, 24))
        self.ui_btn_server_menu.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.ui_btn_server_menu.setAutoRaise(False)
        self.ui_btn_server_menu.triggered.connect(self.ui_btn_server_menu.setDefaultAction)

        server_menu = QMenu(self)
        server_actions = []
        for elem, icon, label, tip in (
            (
                RouterWidgetElems.SERVER_INFO,
                ":images/themes/default/mActionPropertiesWidget.svg",
                "Graph && server info",
                "Show graph & server info",
            ),
            (
                RouterWidgetElems.SERVER_LOG,
                ":images/themes/default/mMessageLog.svg",
                "Server log",
                "View local server logs",
            ),
        ):
            action = QAction(get_icon(icon), label, self)
            action.setObjectName(elem.value)
            action.setToolTip(tip)
            setattr(self, elem.value, action)
            server_menu.addAction(action)
            server_actions.append(action)

        self.ui_btn_server_menu.setMenu(server_menu)
        self.ui_btn_server_menu.setDefaultAction(server_actions[0])
        self.server_layout.addWidget(self.ui_btn_server_menu)

        # graph-extent as a normal tool button, right of the menu
        self.server_layout.addWidget(
            add_btn(
                RouterWidgetElems.SERVER_GRAPH_EXTENT.value,
                "graph_extent_icon.svg",
                "Loads the current graph extent as polygon layer and checks for things like admins & tz dbs",
                False,
            )
        )

        self.outer_layout.addRow("Local Server", self.server_layout)

        # the lower row, i.e. profiles
        mode_buttons = {
            RouterWidgetElems.PED: ("pedestrian.svg", "Pedestrian mode"),
            RouterWidgetElems.BIKE: ("bike.svg", "Bike mode"),
            RouterWidgetElems.CAR: ("car.svg", "Car mode"),
            RouterWidgetElems.TRUCK: ("truck.svg", "Truck mode"),
            RouterWidgetElems.MBIKE: ("motorbike.svg", "Motorbike mode"),
            RouterWidgetElems.BUS: ("bus.svg", "Bus mode"),
        }
        self.profile_layout = QHBoxLayout(self)
        self.mode_btns = QButtonGroup()
        self.mode_btns.setExclusive(True)
        for btn_enum, (icon, tip) in mode_buttons.items():
            btn = add_btn(btn_enum.value, icon, tip)
            self.mode_btns.addButton(btn)
            self.profile_layout.addWidget(btn)
        self.mode_btns.buttons()[0].setChecked(True)  # set pedestrian as checked button

        self.profile_layout.insertSpacerItem(
            len(mode_buttons),
            QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum),
        )
        self.outer_layout.addRow("Profile", self.profile_layout)

    def _on_btn_prov_options_clicked(self):
        dlg = ProviderDialog(self)
        dlg.exec()
        # refresh the combobox
        self._populate_providers()
