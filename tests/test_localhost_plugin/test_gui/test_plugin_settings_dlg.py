import time
import unittest
from unittest import mock

from qgis.PyQt.QtCore import QEventLoop, QTimer

from ...utilities import get_qgis_app

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.core.pypi import PYPI_PKGS, PyPiState  # noqa: E402
from valhalla.exceptions import PyPiError  # noqa: E402
from valhalla.gui import dlg_plugin_settings  # noqa: E402


def wait(msecs: int):
    loop = QEventLoop()
    QTimer.singleShot(msecs, loop.quit)
    loop.exec()


class TestDepsInstall(unittest.TestCase):
    """The deps table installs in the background with a spinner, install() itself is mocked."""

    def setUp(self):
        self.dlg = dlg_plugin_settings.PluginSettingsDialog()
        self.pkg = PYPI_PKGS[-1]

    def test_spinner(self):
        with mock.patch.object(dlg_plugin_settings, "install", lambda *_: time.sleep(1)):
            self.dlg._on_pypi_install(self.pkg, PyPiState.NOT_INSTALLED)
            btns = self.dlg._install_btns
            # the others can't start a second install meanwhile
            self.assertEqual(
                [b.isEnabled() for name, b in btns.items() if name != self.pkg.pypi_name],
                [False] * (len(btns) - 1),
            )

            icons = set()
            timer = QTimer()
            timer.timeout.connect(lambda: icons.add(btns[self.pkg.pypi_name].icon().cacheKey()))
            timer.start(50)
            wait(800)
            self.assertGreater(len(icons), 3, "the button doesn't spin")
            self.assertIs(self.dlg._installing, self.pkg)

            wait(1500)
            timer.stop()
        self.assertIsNone(self.dlg._installing)
        self.assertIn("Successfully installed", self.dlg.status_bar.currentItem().text())

    def test_failure(self):
        def fail(*_):
            raise PyPiError("boom", detail="details")

        with mock.patch.object(dlg_plugin_settings, "install", fail):
            self.dlg._on_pypi_install(self.pkg, PyPiState.NOT_INSTALLED)
            wait(1000)
        self.assertIsNone(self.dlg._installing)
        self.assertIn("boom", self.dlg.status_bar.currentItem().text())
