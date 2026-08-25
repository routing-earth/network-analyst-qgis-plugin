"""
One-shot "Valhalla is now Network Analyst" notice, shown on the first startup
after upgrading across the 6.0.0 rename (and on a fresh install, which also has
no ``last_seen_version`` recorded yet). The seen version is persisted so it
never re-appears — future upgrades are >= the rename threshold.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import PLUGIN_NAME, __version__
from ..core.settings import ValhallaSettings
from ..global_definitions import Dialogs
from ..utils.resource_utils import get_icon

REBRAND_VERSION = (6, 0, 0)
LAST_SEEN_KEY = "last_seen_version"


def _as_tuple(version: str) -> tuple:
    """Best-effort dotted-version → int tuple; empty/garbage sorts as oldest."""
    try:
        return tuple(int(p) for p in str(version).split(".")[:3])
    except (ValueError, AttributeError):
        return ()


def maybe_show_rebrand_notice(parent=None) -> None:
    """Show the notice once if we're crossing (or landing fresh on) the rename,
    then record the current version so it doesn't show again."""
    settings = ValhallaSettings()
    last_seen = settings.get(Dialogs.SETTINGS, LAST_SEEN_KEY)

    if _as_tuple(last_seen) < REBRAND_VERSION:
        RebrandNoticeDialog(parent).exec()

    settings.set(Dialogs.SETTINGS, LAST_SEEN_KEY, __version__)


class RebrandNoticeDialog(QDialog):
    """Old logo + name  →  new logo + name, with a short reassurance blurb."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Welcome to {PLUGIN_NAME}")

        heading = QLabel(f"<b>The Valhalla QGIS plugin is now {PLUGIN_NAME}.</b>", self)
        heading.setTextFormat(Qt.TextFormat.RichText)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._brand("valhalla_old.svg", "Valhalla\nQGIS plugin"))
        arrow = QLabel("→", self)
        arrow.setStyleSheet("font-size: 24px;")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(arrow)
        row.addWidget(self._brand("valhalla_logo.svg", PLUGIN_NAME))
        row.addStretch(1)

        blurb = QLabel(
            "Same plugin, new name. All your routing, isochrone, matrix and other "
            "tools work exactly as before. It now also supports "
            '<a href="https://routing-earth.com">routing.earth</a> graph packages.',
            self,
        )
        blurb.setWordWrap(True)
        blurb.setTextFormat(Qt.TextFormat.RichText)
        blurb.setOpenExternalLinks(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, self)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addSpacing(6)
        layout.addLayout(row)
        layout.addSpacing(6)
        layout.addWidget(blurb)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)

    def _brand(self, icon_file: str, caption: str) -> QWidget:
        """A logo above its centered caption, as a single stacked widget."""
        wrapper = QWidget(self)
        inner = QVBoxLayout(wrapper)
        inner.setContentsMargins(0, 0, 0, 0)
        logo = QLabel(wrapper)
        logo.setPixmap(get_icon(icon_file).pixmap(56, 56))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel(caption, wrapper)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(logo)
        inner.addWidget(text)
        return wrapper
