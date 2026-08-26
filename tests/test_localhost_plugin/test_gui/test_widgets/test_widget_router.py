import socket
from time import sleep
from unittest import skip
from urllib.parse import urlparse

from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import QProcess, Qt
from qgis.PyQt.QtTest import QTest

from valhalla.core.settings import ValhallaSettings

from .... import URL, LocalhostPluginTestCase
from ....utilities import get_qgis_app

CANVAS: QgsMapCanvas
QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.gui.dock_routing import RoutingDockWidget


def try_connection(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, port))


class TestRouterWidget(LocalhostPluginTestCase):
    def setUp(self) -> None:
        # Berlin
        CANVAS.setExtent(QgsRectangle(1478686, 6885333, 1500732, 6903232))
        CANVAS.setDestinationCrs(QgsCoordinateReferenceSystem.fromEpsgId(3857))

        self.dlg = RoutingDockWidget(IFACE)

        # TODO: need to add a graph before starting the service!
        #       then remove the "skip" decorators below

        return super(TestRouterWidget, self).setUp()

    def tearDown(self):
        if self.dlg.router_widget.valhalla_service.isOpen():
            self.dlg.router_widget.valhalla_service.kill()
        return super().tearDown()

    @skip
    def test_server_start_stop(self):
        QTest.mouseClick(self.dlg.router_widget.ui_btn_server_start, Qt.MouseButton.LeftButton)
        self.assertEqual(self.dlg.router_widget.valhalla_service.state(), QProcess.ProcessState.Starting)
        sleep(0.5)

        # test that it's reachable
        parsed_url = urlparse(URL)
        try_connection(parsed_url.hostname, parsed_url.port)

        QTest.mouseClick(
            self.dlg.router_widget.ui_btn_server_start, Qt.MouseButton.LeftButton
        )  # toggles to stop
        sleep(0.5)

        # test that it's _not_ reachable
        with self.assertRaises(ConnectionRefusedError):
            try_connection(parsed_url.hostname, parsed_url.port)

    @skip
    def test_server_log(self):
        QTest.mouseClick(self.dlg.router_widget.ui_btn_server_start, Qt.MouseButton.LeftButton)
        sleep(0.1)

        self.assertIn("Started ", self.dlg.router_widget.dlg_server_log.text_log.toPlainText())

        QTest.mouseClick(
            self.dlg.router_widget.ui_btn_server_start, Qt.MouseButton.LeftButton
        )  # toggles to stop
        sleep(0.1)

        self.assertIn(
            "Stopping valhalla service", self.dlg.router_widget.dlg_server_log.text_log.toPlainText()
        )

    def test_nonexistent_binaries(self):
        current_binary_dir = ValhallaSettings().get_binary_dir()
        ValhallaSettings().set_binary_dir("bla")

        QTest.mouseClick(self.dlg.router_widget.ui_btn_server_start, Qt.MouseButton.LeftButton)
        sleep(0.1)

        self.assertEqual(
            self.dlg.router_widget.valhalla_service.state(), QProcess.ProcessState.NotRunning
        )
        self.assertIn("pyvalhalla is not installed.", self.dlg.status_bar.currentItem().text())
        parsed_url = urlparse(URL)
        with self.assertRaises(ConnectionRefusedError):
            try_connection(parsed_url.hostname, parsed_url.port)

        ValhallaSettings().set_binary_dir(current_binary_dir)
