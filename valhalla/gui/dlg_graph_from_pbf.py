from pathlib import Path

from qgis.core import Qgis
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog

from ..core.settings import ValhallaSettings
from . import UI_RESOURCE_PATH

GENERATED_FORM_CLASS, _ = uic.loadUiType(str(UI_RESOURCE_PATH / "dlg_graph_from_pbf.ui"))


class GraphFromPBFDialog(QDialog, GENERATED_FORM_CLASS):
    """Collects the inputs for a local graph build — registration and the
    build orchestration live in the LocalGraphController."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._parent = parent
        self.setupUi(self)

        self.pbf_path: str = ""

    @property
    def graph_name(self) -> str:
        return self.ui_text_name.text().strip()

    @property
    def data_dir(self) -> Path:
        """The graph's self-contained subdir in the library."""
        return ValhallaSettings().get_graph_dir().joinpath(self.graph_name)

    # override
    def accept(self):
        self.pbf_path = self.ui_pbf_file.filePath()
        if not self.pbf_path:
            self._parent.status_bar.pushMessage(
                "No PBF", "Needs a PBF file", Qgis.MessageLevel.Critical, 6
            )
            return super().reject()
        elif not self.graph_name:
            self._parent.status_bar.pushMessage(
                "No Graph name", "Needs a graph name", Qgis.MessageLevel.Critical, 6
            )
            return super().reject()

        return super().accept()
