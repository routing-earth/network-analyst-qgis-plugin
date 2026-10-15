from typing import Any, Dict, Optional

from qgis.core import QgsFieldProxyModel, QgsMapLayerProxyModel, QgsVectorLayer
from qgis.gui import QgsFieldComboBox
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QComboBox, QDialog

from . import UI_RESOURCE_PATH
from .widgets.waypoint_model import ROUTING, WaypointTableKind

GENERATED_FORM_CLASS, _ = uic.loadUiType(str(UI_RESOURCE_PATH / "dlg_from_layer.ui"))


class FromLayerDialog(QDialog, GENERATED_FORM_CLASS):
    """
    Picks a point layer to import. For a table kind with add modes (e.g. facility/demand) it
    also asks which one, and offers to fill the kind's layer_fields from the layer's fields.
    """

    def __init__(self, parent=None, kind: WaypointTableKind = ROUTING):
        super(FromLayerDialog, self).__init__(parent)
        self.setupUi(self)
        self.from_layer.setFilters(QgsMapLayerProxyModel.Filter.PointLayer)

        self.layer: Optional[QgsVectorLayer] = None
        # the attributes every imported point gets, e.g. {"role": "facility"}
        self.attrs: Dict[str, Any] = dict()
        # column key -> the layer field to read it from
        self.field_map: Dict[str, str] = dict()

        self.kind = kind
        self.mode_combo: Optional[QComboBox] = None
        if kind.add_modes:
            self.mode_combo = QComboBox(self)
            for mode in kind.add_modes:
                self.mode_combo.addItem(mode.plural.capitalize(), mode.attrs)
            self.formLayout.addRow("Import as", self.mode_combo)

        self.field_combos: Dict[str, QgsFieldComboBox] = dict()
        for layer_field in kind.layer_fields:
            combo = QgsFieldComboBox(self)
            combo.setAllowEmptyFieldName(True)
            if layer_field.numeric:
                combo.setFilters(QgsFieldProxyModel.Filter.Numeric)
            combo.setLayer(self.from_layer.currentLayer())
            self.from_layer.layerChanged.connect(combo.setLayer)
            self.formLayout.addRow(layer_field.label, combo)
            self.field_combos[layer_field.key] = combo

        self.adjustSize()

    def done(self, r: int = 0):
        if r == QDialog.DialogCode.Accepted:
            self.layer = self.from_layer.currentLayer()
            if self.mode_combo is not None:
                self.attrs = dict(self.mode_combo.currentData())
            self.field_map = {
                key: c.currentField() for key, c in self.field_combos.items() if c.currentField()
            }

        super().done(r)
