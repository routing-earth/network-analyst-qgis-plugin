from pathlib import Path
from urllib.parse import urlparse

from qgis.core import Qgis
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog

from ..core.settings import ValhallaSettings
from . import UI_RESOURCE_PATH

GENERATED_FORM_CLASS, _ = uic.loadUiType(str(UI_RESOURCE_PATH / "dlg_graph_from_url.ui"))


class GraphFromURLDialog(QDialog, GENERATED_FORM_CLASS):
    """Collects the inputs for a tile_url graph — registration lives in the
    LocalGraphController."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._parent = parent
        self.setupUi(self)

    @property
    def url(self) -> str:
        return self.ui_text_url.text().strip()

    @property
    def graph_name(self) -> str:
        return self.ui_text_name.text().strip() or urlparse(self.url).netloc

    @property
    def cache_dir(self) -> Path:
        """The graph's self-contained subdir in the library (tile cache)."""
        return ValhallaSettings().get_graph_dir().joinpath(self.graph_name)

    @property
    def user_pw(self) -> str:
        if (user := self.ui_text_user.text()) and (pw := self.ui_text_password.text()):
            return f"{user}:{pw}"
        return ""

    # override
    def accept(self):
        if not self.url or not urlparse(self.url).scheme:
            self._parent.status_bar.pushMessage(
                "No URL", "Needs a valid HTTP(s) URL", Qgis.MessageLevel.Critical, 6
            )
            return super().reject()

        return super().accept()
