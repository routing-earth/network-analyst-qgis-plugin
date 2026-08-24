import json
import tarfile
import unittest
from io import BytesIO
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from unittest import mock

from valhalla.core import graph_registry

from ...utilities import get_qgis_app

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()


def _make_managed_tar(path: Path, scope="germany", cadence="daily", dataset_id=1700000000):
    """A tar whose second member is the routing-earth identity (behind index.bin)."""
    with tarfile.open(path, "w") as tar:
        for name, payload in (
            ("index.bin", b"\x00"),
            (
                graph_registry.RE_STATE_MEMBER,
                json.dumps({"scope": scope, "cadence": cadence, "dataset_id": dataset_id}).encode(),
            ),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, BytesIO(payload))


class TestGraphRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(mkdtemp())
        self.lib = self.tmp.joinpath("graphs")
        self.lib.mkdir()
        self.patcher = mock.patch("valhalla.core.graph_registry.graph_dir", side_effect=lambda: self.lib)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        rmtree(self.tmp, ignore_errors=True)

    def test_local_vs_re(self):
        graph_registry.register(
            "andorra", graph_registry.local_graph_config(tile_extract="/x/andorra.tar")
        )
        graph_registry.register(
            "germany_daily",
            graph_registry.re_graph_config("/x/germany_daily.tar", "germany", "daily"),
        )
        entries = {e.name: e for e in graph_registry.discover()}
        self.assertEqual(set(entries), {"andorra", "germany_daily"})
        self.assertFalse(entries["andorra"].is_re)
        self.assertTrue(entries["germany_daily"].is_re)
        self.assertEqual(entries["germany_daily"].scope, "germany")
        self.assertEqual(entries["germany_daily"].cadence, "daily")

    def test_set_re_state_round_trip(self):
        entry_dir = graph_registry.register(
            "germany_daily", graph_registry.re_graph_config("/x/g.tar", "germany", "daily")
        )
        graph_registry.set_re_state(entry_dir, osm_data_timestamp="2026-08-01", last_diff="5.0 MiB")
        graph_registry.mark_synced(entry_dir, behind="")
        entry = graph_registry.discover()[0]
        self.assertEqual(entry.osm_data_timestamp, "2026-08-01")
        self.assertEqual(entry.last_diff, "5.0 MiB")
        self.assertTrue(entry.synced_at)  # stamped by mark_synced

    def test_register_collision_and_replace(self):
        graph_registry.register("dupe", graph_registry.local_graph_config(tile_extract="/a.tar"))
        with self.assertRaises(FileExistsError):
            graph_registry.register("dupe", graph_registry.local_graph_config(tile_extract="/b.tar"))
        # an RE config, then a replace with a local config drops the routing_earth block
        graph_registry.register(
            "dupe", graph_registry.re_graph_config("/a.tar", "germany", "daily"), replace=True
        )
        self.assertTrue(graph_registry.discover()[0].is_re)
        graph_registry.register(
            "dupe", graph_registry.local_graph_config(tile_extract="/b.tar"), replace=True
        )
        entry = graph_registry.discover()[0]
        self.assertEqual(entry.tile_extract, "/b.tar")
        self.assertFalse(entry.is_re)

    def test_unregister_deletes_the_whole_dir(self):
        entry_dir = graph_registry.register(
            "with_data", graph_registry.local_graph_config(tile_extract="data.tar")
        )
        entry_dir.joinpath("data.tar").write_bytes(b"fake")
        graph_registry.unregister("with_data")
        self.assertFalse(graph_registry.entry_exists("with_data"))
        self.assertFalse(entry_dir.exists())  # data goes with the entry

    def test_managed_tar_detection(self):
        managed = self.tmp.joinpath("germany_daily.tar")
        _make_managed_tar(managed)
        state = graph_registry.read_tar_state(managed)
        self.assertEqual(state["scope"], "germany")
        self.assertEqual(state["dataset_id"], 1700000000)

        plain = self.tmp.joinpath("plain.tar")
        with tarfile.open(plain, "w") as tar:
            info = tarfile.TarInfo("index.bin")
            info.size = 1
            tar.addfile(info, BytesIO(b"\x00"))
        self.assertIsNone(graph_registry.read_tar_state(plain))

    def test_is_managed_reads_tar(self):
        managed = self.lib.joinpath("germany_daily", "germany_daily.tar")
        managed.parent.mkdir(parents=True)
        _make_managed_tar(managed)
        graph_registry.register(
            "germany_daily",
            graph_registry.re_graph_config(str(managed.resolve()), "germany", "daily"),
        )
        entry = graph_registry.discover()[0]
        self.assertTrue(entry.is_managed)
        self.assertEqual(entry.dataset_id, 1700000000)
