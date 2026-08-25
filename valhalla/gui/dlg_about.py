from datetime import datetime
from typing import Optional

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog

from .. import PLUGIN_NAME, __version__
from ..core.graph_registry import discover, humanize_timestamp
from ..utils.http_utils import get_status_response
from . import UI_RESOURCE_PATH

GENERATED_FORM_CLASS, _ = uic.loadUiType(str(UI_RESOURCE_PATH / "dlg_about.ui"))


class AboutDialog(QDialog, GENERATED_FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._parent = parent
        self.setupUi(self)
        self.setWindowTitle(f"About {PLUGIN_NAME}")
        self.ui_plugin_version_text.setText(__version__)

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
