from functools import partial
from pathlib import Path
from traceback import format_exception
from typing import Optional, Tuple

from packaging.version import parse as Version
from qgis.core import Qgis
from qgis.gui import QgisInterface, QgsCollapsibleGroupBox, QgsFileWidget
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QRect, QSize, Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.pypi import (
    PYPI_PKGS,
    PyPiPkg,
    PyPiState,
    compare,
    install,
    installed_version,
    pypi_version,
)
from ..core.settings import ValhallaSettings
from ..exceptions import PyPiError
from ..global_definitions import Dialogs
from ..utils.logger_utils import qgis_log
from ..utils.resource_utils import (
    check_valhalla_installation,
    get_default_valhalla_binary_dir,
    get_icon,
)
from . import UI_RESOURCE_PATH
from .gui_utils import add_msg_bar
from .widgets.widget_graph_manager import GraphManagerWidget

GENERATED_FORM_CLASS, _ = uic.loadUiType(str(UI_RESOURCE_PATH / "dlg_plugin_settings.ui"))


iface: QgisInterface


class PluginSettingsDialog(QDialog, GENERATED_FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        try:
            # Qt6/PyQt6: enum values live inside the Option sub-class
            _opts = (
                QFileDialog.Option.DontResolveSymlinks
                | QFileDialog.Option.ReadOnly
                | QFileDialog.Option.ShowDirsOnly
            )
        except AttributeError:
            # Qt5/PyQt5: enum values live directly on QFileDialog
            _opts = QFileDialog.DontResolveSymlinks | QFileDialog.ReadOnly | QFileDialog.ShowDirsOnly
        self.ui_binary_path.setOptions(_opts)
        # the status bar inserts itself at layout index 0, build it before the table
        self.status_bar = add_msg_bar(self.main_layout)
        self.setupDepsTable()

        # the unified graphs section (local + routing-earth.com), fully built in code
        self.ui_graphs_group = QgsCollapsibleGroupBox("Graphs: routing-earth.com && local")
        self.ui_graphs_group.setObjectName("ui_re_group")
        self.graph_widget = GraphManagerWidget(self)
        QVBoxLayout(self.ui_graphs_group).addWidget(self.graph_widget)
        # index 2: the message bar sits at 0, the binaries form at 1
        self.main_layout.insertWidget(2, self.ui_graphs_group)

        # expanded boxes absorb the vertical space (stretch 10 vs 1); with all
        # of them collapsed the trailing stretch swallows it instead, so the
        # collapsed headers stack at the top, not the bottom
        self.main_layout.setStretchFactor(self.status_bar, 0)
        for box in (self.ui_graphs_group, self.ui_deps_group):
            self.main_layout.setStretchFactor(box, 10)
        self.main_layout.addStretch(1)

        self.ui_btn_default_binary_path.setIcon(get_icon(":images/themes/default/mIconPythonFile.svg"))
        btn_size = self.ui_binary_path.height()
        self.ui_btn_default_binary_path.setFixedSize(btn_size, btn_size)
        self.ui_btn_default_binary_path.setIconSize(QSize(btn_size - 2, btn_size - 2))
        self.ui_binary_path.setFilePath(str(ValhallaSettings().get_binary_dir()))

        # connections
        self.ui_btn_default_binary_path.clicked.connect(self._on_default_binary_path)
        self.ui_binary_path: QgsFileWidget
        self.ui_binary_path.fileChanged.connect(self._on_binary_path_change)

    def _on_binary_path_change(self, path: str):
        settings = ValhallaSettings()
        old_path = settings.get_binary_dir()
        new_path = Path(path)
        settings.set_binary_dir(new_path)
        if not check_valhalla_installation():
            self.status_bar.pushMessage(
                f"Couldnt find valhalla_service in {new_path}",
                level=Qgis.MessageLevel.Warning,
                duration=5,
            )
            settings.set_binary_dir(old_path)

    def _on_default_binary_path(self):
        default_path = get_default_valhalla_binary_dir()
        ValhallaSettings().set_binary_dir(default_path)
        self.ui_binary_path.setFilePath(str(default_path))

    def setupDepsTable(self):
        """
        Set up deps table. Building it may never raise: a package we can't look
        up online is shown as "unknown", not as a traceback in the user's face.
        """
        self.ui_deps_table.clear()
        self.ui_deps_table.setRowCount(len(PYPI_PKGS))
        self.ui_deps_table.setHorizontalHeaderLabels(["Package", "Installed", "Available", "Action"])
        unavailable_pkgs = []
        for row_id, pkg in enumerate(PYPI_PKGS):
            # get the versions and the currently installed state; available is
            # None when PyPI couldn't be reached, which is not an error
            local, available, installed_state = self._pkg_row(pkg)
            current_version = Version(local or "0.0.0")
            if available is None:
                unavailable_pkgs.append(pkg.pypi_name)
            if installed_state == PyPiState.NOT_INSTALLED:
                # installing is still worth offering when we couldn't reach PyPI
                icon = ":images/themes/default/pluginNew.svg"
                tooltip = f"Install {pkg.pypi_name}"
                if available is None:
                    tooltip += " (couldn't reach PyPI to check the version)"
            elif available is None:
                icon = ":images/themes/default/mIconWarning.svg"
                tooltip = f"Couldn't reach PyPI, not sure if {pkg.pypi_name} is up to date"
            elif installed_state == PyPiState.UPGRADEABLE:
                icon = ":images/themes/default/pluginUpgrade.svg"
                tooltip = f"Upgrade {pkg.pypi_name} to {available.public}"
            else:
                icon = ":images/themes/default/algorithms/mAlgorithmCheckGeometry.svg"
                tooltip = f"{pkg.pypi_name} is at the latest version"

            # add a URL linked label
            url_label = QLabel(f'<a href="{pkg.url}">{pkg.pypi_name}</a>')
            url_label.setTextFormat(Qt.TextFormat.RichText)
            url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            url_label.setOpenExternalLinks(True)
            self.ui_deps_table.setCellWidget(row_id, 0, url_label)
            version_item = QTableWidgetItem(current_version.public)
            version_item.setToolTip(current_version.public)
            self.ui_deps_table.setItem(row_id, 1, version_item)
            available_text = available.public if available else "unknown"
            available_item = QTableWidgetItem(available_text)
            available_item.setToolTip(available_text)
            self.ui_deps_table.setItem(row_id, 2, available_item)

            # add a tool button for the download
            btn = QToolButton()
            btn.rect = QRect(10, 10, 10, 10)
            btn.setIcon(get_icon(icon))
            btn.setEnabled(installed_state != PyPiState.UP_TO_DATE)
            btn.setToolTip(tooltip)
            f = partial(self._on_pypi_install, pkg, installed_state)
            btn.clicked.connect(f)
            self.ui_deps_table.setCellWidget(row_id, 3, btn)

        self.ui_deps_table.resizeColumnToContents(3)

        if unavailable_pkgs:
            self.status_bar.pushMessage(
                f"Couldn't reach PyPI to check {', '.join(unavailable_pkgs)}",
                Qgis.MessageLevel.Warning,
                5,
            )

    def _pkg_row(self, pkg: PyPiPkg) -> Tuple[Optional[str], Optional[Version], PyPiState]:
        """
        The (installed, available, state) triple for one row — both versions
        None-able, looked up once, and never raising: the dialog has to open
        even when we can't tell what's going on.
        """
        try:
            local, available = installed_version(pkg), pypi_version(pkg)
            return local, available, compare(local, available)
        except Exception as e:  # noqa: BLE001 - the dialog must open regardless
            self._log_failure(f"Couldn't determine the {pkg.pypi_name} version", e)
            return None, None, PyPiState.NOT_INSTALLED

    def _on_pypi_install(self, pkg: PyPiPkg, installed_state: PyPiState):
        """Install/upgrade the package (pyvalhalla or routing-earth-utils)."""
        try:
            install(pkg, installed_state)
        except PyPiError as e:
            self._log_failure(f"Couldn't install {pkg.pypi_name}: {e}", e, e.detail)
            return
        except Exception as e:  # noqa: BLE001 - never let a traceback dialog pop
            self._log_failure(f"Couldn't install {pkg.pypi_name}", e)
            return

        self.status_bar.pushMessage(f"Successfully installed/upgraded package: {pkg.pypi_name}")
        # update the table with the new info
        self.setupDepsTable()
        QApplication.processEvents()

    def _log_failure(self, message: str, exc: Exception, detail: str = ""):
        """One line in the message bar, everything we know in the log panel."""
        self.status_bar.pushMessage(f"{message} (see the log panel)", Qgis.MessageLevel.Critical, 0)
        trace = "".join(format_exception(type(exc), exc, exc.__traceback__))
        qgis_log(f"{message}\n{detail}\n{trace}", Qgis.MessageLevel.Critical)

    def on_settings_change(self, new_text, widget: Optional[QWidget] = ""):
        attr = widget.objectName() if widget else self.sender().objectName()
        ValhallaSettings().set(Dialogs.SETTINGS, attr, str(new_text))
