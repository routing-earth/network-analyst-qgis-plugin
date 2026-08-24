"""
The unified "Graphs" section of the plugin settings dialog: ONE table for
local graphs and routing-earth.com packages (plus not-yet-downloaded
entitlements), backed by the graph registry (core/graph_registry.py — no
global graph dir, data lives anywhere). Op logic lives in the two
controllers (graph_ops_re.py / graph_ops_local.py); this file is chrome and
wiring only.
"""

from qgis.core import Qgis
from qgis.gui import QgsPasswordLineEdit
from qgis.PyQt.QtCore import QFileSystemWatcher, QProcess, Qt
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableView,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import graph_registry
from ...core.routing_earth import fetch_entitlements, set_re_api_key
from ...core.settings import ValhallaSettings
from ...utils.resource_utils import get_icon
from ..dlg_config_editor import ConfigEditorDialog
from .graph_ops_local import LocalGraphController
from .graph_ops_re import RoutingEarthController
from .graph_table_model import COLUMNS, NOT_MANAGED_TOOLTIP, GraphTableModel
from .widget_splitter import SplitterWithHandleButton

ACTION_COL = len(COLUMNS) - 1
FOLDER_TOOLTIP = "Set the graph library directory\nCurrently: {}"


class GraphManagerWidget(QWidget):
    def __init__(self, parent):
        """:param parent: the PluginSettingsDialog (provides status_bar)"""
        super().__init__(parent)
        self._parent = parent

        self.model = GraphTableModel(self)
        self._setup_ui()

        self.re_ctl = RoutingEarthController(
            self, self.log_widget.append, parent.status_bar, self.model.refresh, self._confirm_replace
        )
        self.local_ctl = LocalGraphController(
            self, self.log_widget.append, parent.status_bar, self.model.refresh, self._confirm_replace
        )
        self._setup_menu()

        self.model.modelReset.connect(self._rebuild_action_widgets)
        # grey out remove unless a registered (i.e. downloaded) row is selected
        self.ui_table.selectionModel().selectionChanged.connect(self._update_remove_enabled)
        self.model.modelReset.connect(self._update_remove_enabled)
        self._update_remove_enabled()
        self.graph_watcher = QFileSystemWatcher([str(graph_registry.graph_dir())], self)
        self.graph_watcher.directoryChanged.connect(self.model.refresh)
        self.model.refresh()

    def shutdown(self):
        """Kill any in-flight subprocess on plugin unload/reload so none linger
        as a stale, 'Busy'-reading QProcess (see dock_routing.unload)."""
        procs = (
            self.re_ctl.proc,
            self.local_ctl.valhalla_build_admins,
            self.local_ctl.valhalla_build_tiles,
        )
        for proc in procs:
            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
                proc.waitForFinished(2000)

    # ---------------- UI assembly ----------------

    def _setup_ui(self):
        # API key (auth database) + API URL row
        self.ui_text_api_key = QgsPasswordLineEdit(self)
        self._reset_api_key_placeholder()
        self.ui_text_api_key.editingFinished.connect(self._on_api_key_edited)
        self.ui_text_api_url = QLineEdit(ValhallaSettings().get_re_api_url(), self)
        self.ui_text_api_url.setToolTip("routing.earth API origin")
        self.ui_text_api_url.editingFinished.connect(
            lambda: ValhallaSettings().set_re_api_url(self.ui_text_api_url.text().strip())
        )

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API key", self))
        key_row.addWidget(self.ui_text_api_key, 2)
        key_row.addWidget(QLabel("URL", self))
        key_row.addWidget(self.ui_text_api_url, 1)

        # tool buttons (the add-menu is wired once the controllers exist)
        self.ui_btn_add = QToolButton(self)
        self.ui_btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.ui_btn_add.setAutoRaise(False)

        self.ui_btn_remove = QToolButton(self)
        self.ui_btn_remove.setIcon(get_icon("graph_remove.svg"))
        self.ui_btn_remove.setToolTip("Remove the selected graph")
        self.ui_btn_remove.clicked.connect(self._on_remove)

        self.ui_btn_folder = QToolButton(self)
        self.ui_btn_folder.setIcon(get_icon("graph_folder.svg"))
        self.ui_btn_folder.setToolTip(FOLDER_TOOLTIP.format(ValhallaSettings().get_graph_dir()))
        self.ui_btn_folder.clicked.connect(self._on_change_graph_dir)

        self.config_dlg = ConfigEditorDialog(self._parent)
        self.ui_btn_settings = QToolButton(self)
        self.ui_btn_settings.setIcon(get_icon(":images/themes/default/console/iconSettingsConsole.svg"))
        self.ui_btn_settings.setToolTip("Edit the local Valhalla configuration")
        self.ui_btn_settings.clicked.connect(self.config_dlg.exec)

        btn_row = QHBoxLayout()
        for btn in (self.ui_btn_add, self.ui_btn_remove, self.ui_btn_folder, self.ui_btn_settings):
            btn_row.addWidget(btn)
        btn_row.addStretch(1)

        # the graphs table
        self.ui_table = QTableView(self)
        self.ui_table.setModel(self.model)
        self.ui_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.ui_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.ui_table.setShowGrid(False)
        self.ui_table.verticalHeader().setVisible(False)
        # data columns share the width evenly; the action column hugs its button
        header = self.ui_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(ACTION_COL, QHeaderView.ResizeMode.ResizeToContents)

        # the client/build log in a collapsible splitter next to the table
        self.log_widget = QTextBrowser(self)
        self.splitter = SplitterWithHandleButton(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.ui_table)
        self.splitter.addWidget(self.log_widget)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, True)
        if state := ValhallaSettings().get_re_splitter_state():
            self.splitter.restoreState(state)
        else:
            self.splitter.setSizes([1, 0])
        expanded = self.splitter.sizes()[1] > 0
        self.splitter.handle_button.setIcon(
            get_icon("triangle_right.svg" if expanded else "triangle_left.svg")
        )
        self.splitter.handle_button.setChecked(expanded)
        self.splitter.handle_button.setToolTip("Toggle the graph operations log")
        self.splitter.handle_button.toggled.connect(self._toggle_splitter_button)
        self.splitter.splitterMoved.connect(self._save_splitter_state)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(key_row)
        layout.addLayout(btn_row)
        layout.addWidget(self.splitter)

    def _setup_menu(self):
        """Update-from-routing.earth on top, the local build flows behind an
        "Advanced" section.

        NB the section inserts a separator-action — never index
        ``menu.actions()`` positionally; the default action stays pinned."""
        add_menu = QMenu(self)
        update_action = add_menu.addAction(
            get_icon(":images/themes/default/mActionRefresh.svg"), "Update from routing.earth"
        )
        update_action.setToolTip("Check all packages against the routing.earth servers")
        update_action.triggered.connect(self._on_status)
        add_menu.addSection("Advanced")
        add_menu.addAction(get_icon("graph_add_tar.svg"), "From Tar").triggered.connect(
            self.local_ctl.add_tar
        )
        add_menu.addAction(get_icon("graph_add_url.svg"), "From URL").triggered.connect(
            self.local_ctl.add_url
        )
        add_menu.addAction(get_icon("graph_add_build.svg"), "From PBF").triggered.connect(
            self.local_ctl.add_pbf
        )
        self.ui_btn_add.setMenu(add_menu)
        self.ui_btn_add.setDefaultAction(update_action)

    # ---------------- splitter state (survives plugin reloads) ----------------

    def _save_splitter_state(self, *_):
        """
        Persists the state on every change (incl. collapsed) so it survives a
        plugin reload — there's no quit hook to rely on there. Widths of 1-50px
        aren't saved, that drag zone makes for strange restore UX.
        """
        if self.splitter.sizes()[1] > 0:
            self.splitter.handle_button.setIcon(get_icon("triangle_right.svg"))
            self.splitter.handle_button.setChecked(True)
            if self.splitter.sizes()[1] > 50:
                ValhallaSettings().set_re_splitter_state(self.splitter.saveState())
        elif self.splitter.sizes()[1] == 0:
            self.splitter.handle_button.setIcon(get_icon("triangle_left.svg"))
            self.splitter.handle_button.setChecked(False)
            ValhallaSettings().set_re_splitter_state(self.splitter.saveState())

    def _toggle_splitter_button(self, checked: bool):
        settings = ValhallaSettings()
        if checked:
            self.splitter.handle_button.setIcon(get_icon("triangle_right.svg"))
            if self.splitter.sizes()[1] == 0:
                if state := settings.get_re_splitter_state():
                    self.splitter.restoreState(state)
                if self.splitter.sizes()[1] == 0:
                    self.splitter.setSizes([3, 1])
            settings.set_re_splitter_state(self.splitter.saveState())
        else:
            self.splitter.handle_button.setIcon(get_icon("triangle_left.svg"))
            self.splitter.setSizes([1, 0])
            settings.set_re_splitter_state(self.splitter.saveState())

    # ---------------- API key ----------------

    def _reset_api_key_placeholder(self):
        self.ui_text_api_key.clear()
        stored = bool(ValhallaSettings().get_re_authcfg())
        self.ui_text_api_key.setPlaceholderText(
            "stored in the QGIS auth database" if stored else "paste your routing.earth API key"
        )

    def _on_api_key_edited(self):
        key = self.ui_text_api_key.text().strip()
        if not key:
            return
        if set_re_api_key(key):
            self._parent.status_bar.pushMessage(
                "routing.earth API key stored in the QGIS auth database",
                Qgis.MessageLevel.Success,
                3,
            )
        else:
            self._parent.status_bar.pushMessage(
                "Couldn't store the API key in the QGIS auth database",
                Qgis.MessageLevel.Critical,
                0,
            )
        self._reset_api_key_placeholder()

    # ---------------- action column ----------------

    def _rebuild_action_widgets(self):
        """One button per row, recreated on every model reset. Closures hold
        the entry NAME and re-resolve at click time — a watcher-driven reset
        mid-operation must never leave stale entries in them."""
        for row, entry in enumerate(self.model.entries):
            if not entry.is_re:
                continue  # local graphs have no per-row action (yet)
            btn = QToolButton()
            btn.setIcon(get_icon(":images/themes/default/mActionRefresh.svg"))
            btn.setToolTip(
                f"Sync {entry.name} to the latest published state"
                if entry.is_managed
                else NOT_MANAGED_TOOLTIP
            )
            btn.setEnabled(entry.is_managed)
            btn.clicked.connect(lambda _, name=entry.name: self._on_sync(name))
            self.ui_table.setIndexWidget(self.model.index(row, ACTION_COL), btn)
        for i, ent in enumerate(self.model.available):
            row = len(self.model.entries) + i
            btn = QToolButton()
            btn.setIcon(get_icon("graph_add_url.svg"))
            btn.setToolTip(f"Download {ent.scope}/{ent.cadence}...")
            btn.clicked.connect(lambda _, e=ent: self.re_ctl.init_package(e))
            self.ui_table.setIndexWidget(self.model.index(row, ACTION_COL), btn)

    def _on_sync(self, name: str):
        if (entry := self.model.entry_by_name(name)) is not None and entry.is_managed:
            self.re_ctl.sync(entry)

    # ---------------- toolbar ops ----------------

    def _confirm_replace(self, name: str) -> bool:
        """The single duplicate-handling prompt for every add path."""
        return (
            QMessageBox.warning(
                self,
                "Graph exists",
                f"The graph '{name}' is already registered. Should it be replaced?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _on_status(self):
        """One refresh = entitlements via HTTP + `re status` per package."""
        try:
            entitled = fetch_entitlements()
            local = {(e.scope, e.cadence) for e in self.model.entries}
            self.model.set_available([e for e in entitled if (e.scope, e.cadence) not in local])
        except ValueError as e:
            self._parent.status_bar.pushWarning("Entitlements", str(e))

        self.re_ctl.status_all(self.model.entries)

    def _selected_entry(self):
        rows = self.ui_table.selectionModel().selectedRows()
        return self.model.entry_at(rows[0].row()) if rows else None

    def _update_remove_enabled(self, *_):
        self.ui_btn_remove.setEnabled(self._selected_entry() is not None)

    def _on_remove(self):
        entry = self._selected_entry()
        if entry is None:  # button is disabled then, belt & braces
            return

        # the graph is self-contained: removing the entry removes its data
        path = graph_registry.entry_path(entry.name)
        if (
            QMessageBox.warning(
                self,
                "Remove graph",
                f"Remove '{entry.name}' and its data?\n\n{path}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        graph_registry.unregister(entry.name)
        self.model.refresh()
        self._parent.status_bar.pushMessage("Removed graph", entry.name, Qgis.MessageLevel.Warning, 3)

    def _on_change_graph_dir(self):
        new_dir = QFileDialog.getExistingDirectory(
            self, "Select the graph library directory", str(ValhallaSettings().get_graph_dir())
        )
        if not new_dir:
            return
        ValhallaSettings().set_graph_dir(new_dir)
        self.graph_watcher.removePaths(self.graph_watcher.directories())
        self.graph_watcher.addPath(str(graph_registry.graph_dir()))
        self.ui_btn_folder.setToolTip(FOLDER_TOOLTIP.format(new_dir))
        self.model.refresh()
