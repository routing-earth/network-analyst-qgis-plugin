from datetime import datetime
from typing import Optional

from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QPalette, QPixmap
from qgis.PyQt.QtWidgets import QDialog

from .. import PLUGIN_NAME, __version__
from ..core.graph_registry import discover, humanize_timestamp
from ..utils.http_utils import get_status_response
from ..utils.resource_utils import ResGroups, get_resource_path
from . import UI_RESOURCE_PATH

GENERATED_FORM_CLASS, _ = uic.loadUiType(str(UI_RESOURCE_PATH / "dlg_about.ui"))


class AboutDialog(QDialog, GENERATED_FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._parent = parent
        self.setupUi(self)
        self.setWindowTitle(f"About {PLUGIN_NAME}")
        self.ui_plugin_version_text.setText(__version__)

        # author line: routing.earth logo only, linked to the team page. Rendered
        # to a transparent pixmap (a rich-text <img> paints transparent pixels
        # black). The wordmark is near-black, so use the white variant on dark
        # themes for contrast.
        self._set_author_logo()

        self.exception_msg: Optional[str] = None
        self.buttonBox.accepted.connect(self.accept)
        self.ui_valhalla_version_text.setText("NA")
        self.ui_data_age_text.setText("NA")
        self._set_osm_data_age()
        try:
            result = get_status_response(self._parent.router_widget.provider.url)
            valhalla_version: str = result["version"]
            if "-" in valhalla_version:
                std_version, commit_id = valhalla_version.split("-")
                valhalla_version = f'{std_version}-<a href="https://github.com/valhalla/valhalla/commit/{commit_id}">{commit_id}</a>'
            self.ui_valhalla_version_text.setText(valhalla_version)
            self.ui_data_age_text.setText(
                datetime.fromtimestamp(result["tileset_last_modified"]).isoformat() + " UTC"
            )
        except Exception as e:
            self.exception_msg = str(e)

    TEAM_URL = "https://routing-earth.com/team"

    def _set_author_logo(self):
        """Show the routing.earth wordmark (transparent PNG, 439x96) as a
        clickable link to the team page. The wordmark is near-black, so use the
        white variant on dark themes for contrast."""
        is_dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        logo_file = "re_logo_full_dark.png" if is_dark else "re_logo_full.png"
        pixmap = QPixmap(str(get_resource_path(ResGroups.ICONS.value, logo_file)))
        # display ~24px tall, keeping the source crisp on HiDPI
        pixmap.setDevicePixelRatio(pixmap.height() / 24)

        self.label_3.setPixmap(pixmap)
        self.label_3.setToolTip(self.TEAM_URL)
        self.label_3.setCursor(Qt.CursorShape.PointingHandCursor)

        # NB: mousePressEvent overrides a void virtual — the handler must return
        # None. A lambda returning openUrl()'s bool trips sipBadCatcherResult.
        def _open_team(_event):
            QDesktopServices.openUrl(QUrl(self.TEAM_URL))

        self.label_3.mousePressEvent = _open_team

    def _set_osm_data_age(self):
        """OSM extract age of the graph currently selected for the local
        server. Only routing.earth graphs record it (in their ``routing_earth``
        block); locally built graphs (From PBF/Tar/URL) don't, so this stays
        'NA' for them."""
        self.ui_osm_data_text.setText("NA")
        try:
            name = self._parent.router_widget.ui_cmb_graphs.currentText()
        except AttributeError:
            return
        entry = next((e for e in discover() if e.name == name), None)
        if entry and entry.osm_data_timestamp:
            ts = entry.osm_data_timestamp
            self.ui_osm_data_text.setText(f"{humanize_timestamp(ts)} ({ts})")
