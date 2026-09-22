import json
from pathlib import Path

from packaging.version import Version

from valhalla.core.pypi import PYVALHALLA_PKG, PyPiState, check_state
from valhalla.utils.resource_utils import (
    check_valhalla_installation,
    create_valhalla_config,
    get_default_valhalla_binary_dir,
    get_valhalla_config_path,
)

from ... import LocalhostPluginTestCase


class TestResourceUtils(LocalhostPluginTestCase):
    def test_local_lib_version(self):
        # it's the second attribute that will do it
        v = check_state(PYVALHALLA_PKG, Version("0.0.0"))
        self.assertEqual(v, PyPiState.UP_TO_DATE)

        v = check_state(PYVALHALLA_PKG, Version("99.99.99"))
        self.assertEqual(v, PyPiState.UPGRADEABLE)

    def test_create_valhalla_config_failure(self):
        # first test the easy path where it exists and we force
        config_path = get_valhalla_config_path()
        if config_path.exists():
            config_path.unlink()

        config_path.touch(exist_ok=False)

        create_valhalla_config(force=False)

        with config_path.open() as f:
            self.assertEqual(f.read(), "")

        config_path.unlink()

        # now let it create one
        create_valhalla_config()
        with config_path.open() as f:
            j = json.load(f)
            self.assertFalse(j["mjolnir"].get("tile_dir"))
            self.assertFalse(j["mjolnir"].get("tile_extract"))
            self.assertFalse(j["mjolnir"].get("tile_url"))
            self.assertFalse(j["mjolnir"].get("tile_url_user_pw"))
            self.assertTrue(j["service_limits"]["status"]["allow_verbose"])

    def test_check_valhalla_installation_success(self):
        self.assertTrue(check_valhalla_installation())

    def test_get_default_valhalla_binary_dir_success(self):
        default_dir = get_default_valhalla_binary_dir()
        self.assertIsInstance(default_dir, Path)
        self.assertTrue(default_dir.exists())
