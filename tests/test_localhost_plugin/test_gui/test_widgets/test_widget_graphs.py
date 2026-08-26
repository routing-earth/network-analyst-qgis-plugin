from shutil import rmtree
from unittest import mock

from qgis.PyQt.QtTest import QSignalSpy, QTest

from valhalla.gui.dlg_plugin_settings import PluginSettingsDialog

from .... import TEST_DIR, LocalhostPluginTestCase


class TestWidget(LocalhostPluginTestCase):
    def test_pbf_build(self):
        pbf_path = TEST_DIR.joinpath("data", "andorra-latest.osm.pbf")
        self.assertTrue(pbf_path.exists())

        # the graph library is the single source of truth; the build lands in
        # <graph_dir>/<name>/
        lib_dir = pbf_path.parent.joinpath("graph_lib")
        lib_dir.mkdir(exist_ok=True, parents=True)

        with mock.patch(
            "valhalla.core.settings.ValhallaSettings.get_graph_dir", side_effect=lambda: lib_dir
        ):
            settings_dlg = PluginSettingsDialog()
            widget = settings_dlg.graph_widget

            self.assertEqual(widget.model.rowCount(), 0)

            pbf_dlg = widget.local_ctl.from_pbf_dlg
            pbf_dlg.ui_pbf_file.setFilePath(str(pbf_path.resolve()))
            pbf_dlg.ui_text_name.setText("andorra")

            # NB find the action by text — the menu carries a section separator
            build_action = next(a for a in widget.ui_btn_add.menu().actions() if a.text() == "From PBF")
            build_action.trigger()

            pbf_dlg.accept()

            # should finish within 5 secs
            spy_fin = QSignalSpy(widget.local_ctl.valhalla_build_admins.finished)
            self.assertTrue(spy_fin.wait(5000))
            exit_code, _ = spy_fin[-1]
            self.assertEqual(exit_code, 0)

            # should finish within 10 secs
            spy_fin = QSignalSpy(widget.local_ctl.valhalla_build_tiles.finished)
            self.assertTrue(spy_fin.wait(10000))
            exit_code, _ = spy_fin[-1]
            self.assertEqual(exit_code, 0)

            # give it time to update the table
            QTest.qWait(100)

            # the registered graph shows up in the table, data inside the library
            self.assertEqual(widget.model.rowCount(), 1)
            entry = widget.model.entry_at(0)
            self.assertEqual(entry.name, "andorra")
            self.assertFalse(entry.is_re)
            self.assertTrue(entry.tile_dir.startswith(str(lib_dir.joinpath("andorra").resolve())))

        # cleanup
        rmtree(lib_dir)
