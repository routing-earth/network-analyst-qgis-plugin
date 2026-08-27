from typing import List, Optional

from qgis.core import QgsApplication
from qgis.gui import QgisInterface
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import QAction, QMenu, QToolBar

from . import PLUGIN_NAME
from .gui.dlg_rebrand_notice import maybe_show_rebrand_notice
from .gui.dock_routing import RoutingDockWidget
from .processing.provider import ValhallaProvider
from .utils.resource_utils import get_icon


class ValhallaPlugin:
    def __init__(self, iface: QgisInterface):
        """Constructor.

        :param iface: An interface instance that will be passed to this class
            which provides the hook by which you can manipulate the QGIS
            application at run time.
        """
        # Save reference to the QGIS interface
        self.iface = iface

        self.provider = ValhallaProvider()

        self.na_toolbar: Optional[QToolBar] = None
        self.menu: Optional[QMenu] = None
        self.actions: List[QAction] = list()  # type: ignore

        self.routing_dock: Optional[RoutingDockWidget] = None
        # self.settings_dlg: Optional[PluginSettingsDialog] = None
        # self.optimization_dlg: Optional[SpoptDialog] = None

    def add_action(self, icon, title, callback):
        action = QAction(icon, title, self.iface.mainWindow())
        action.triggered.connect(callback)
        self.menu.addAction(action)
        self.na_toolbar.addAction(action)

        self.actions.append(action)

    def initGui(self):
        """Init the user interface."""
        # Add the toolbar
        self.na_toolbar = self.iface.mainWindow().findChild(QToolBar, PLUGIN_NAME)
        if not self.na_toolbar:
            self.na_toolbar = self.iface.addToolBar(PLUGIN_NAME)
            self.na_toolbar.setObjectName(PLUGIN_NAME.replace(" ", "_").lower())

        # Setup menu
        self.menu = QMenu(PLUGIN_NAME)
        valhalla_icon = get_icon("valhalla_logo.svg")
        self.menu.setIcon(valhalla_icon)

        for title, callback, icon in (
            ("Network Analyst", self.open_routing_dlg, valhalla_icon),
        ):
            self.add_action(icon, title, callback)

        self.iface.vectorMenu().addMenu(self.menu)

        # add processing provider
        QgsApplication.processingRegistry().addProvider(self.provider)

        # try a dock widget
        self.routing_dock = RoutingDockWidget(self.iface)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.routing_dock)
        self.routing_dock.setVisible(True)

        # one-shot "Valhalla is now Network Analyst" notice (see the module).
        # Deferred onto the event loop AND shown non-modally so it never blocks
        # QGIS startup — initGui() must return promptly while plugins load.
        # Keep a reference so the non-modal dialog isn't garbage-collected.
        QTimer.singleShot(
            0, lambda: setattr(self, "_rebrand_dlg", maybe_show_rebrand_notice(self.iface.mainWindow()))
        )

    def unload(self):
        """Unload the user interface."""
        for action in self.actions:
            self.iface.vectorMenu().removeAction(action)
            self.na_toolbar.removeAction(action)

        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)

        self.iface.removeDockWidget(self.routing_dock)
        self.routing_dock.unload()

    def open_routing_dlg(self):
        """Create and open the version dialog."""
        self.routing_dock.setVisible(not self.routing_dock.isVisible())

    # def open_optimization_dlg(self):
    #     """Create and open the optimization dialog."""
    #     if not self.optimization_dlg:
    #         self.optimization_dlg = SpoptDialog(self.iface.mainWindow(), self.iface)
    #     self.optimization_dlg.open()

