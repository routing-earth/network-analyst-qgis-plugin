import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Union

from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtCore import QSettings

from .. import PLUGIN_NAME
from ..global_definitions import PYTHON_EXE, Dialogs, RouterType
from ..gui.ui_definitions import PluginSettingsDlgElems
from ..utils.misc_utils import str_to_bool

DEFAULTS = {
    PluginSettingsDlgElems.VALHALLA_HTTP_URL: "https://valhalla1.openstreetmap.de",
    PluginSettingsDlgElems.VALHALLA_HTTP_PARAM: "access_token",
    PluginSettingsDlgElems.DEBUG: "False",
    # PluginSettingsDlgElems.SHOP_HTTP_URL: "http://localhost:8080",
}

PROFILE_TO_OSRM_URL = {
    # RouterProfile.PED: PluginSettingsDlgElems.OSRM_HTTP_URL_PED,
    # RouterProfile.BIKE: PluginSettingsDlgElems.OSRM_HTTP_URL_BIKE,
    # RouterProfile.CAR: PluginSettingsDlgElems.OSRM_HTTP_URL_CAR,
}

IGNORE_PYPI = "ignore_pypi"
PLUGIN_VERSION = "plugin_version"

RE_API_URL_DEFAULT = "https://routing-earth.com"


@dataclass
class ProviderSetting:
    name: str
    url: str
    auth_key: str
    auth_param: str


def get_settings_dir() -> Path:
    """
    Returns the permanent directory for this plugin and creates the graph
    directories if not already done.

    :returns: the permanent directory for this plugin.
    """
    d = (
        Path(QgsApplication.qgisSettingsDirPath())
        .joinpath(PLUGIN_NAME.replace(" ", "_").lower())
        .resolve()
    )
    d.mkdir(exist_ok=True, parents=True)

    return d


DEFAULT_PROVIDERS = [
    ProviderSetting("FOSSGIS", "https://valhalla1.openstreetmap.de", "", "access_key"),  # auth_key
    ProviderSetting("localhost", "http://localhost:8002", "", ""),  # auth_key
]


class ValhallaSettings(QgsSettings):
    def __init__(self):
        super().__init__(
            str(get_settings_dir().joinpath("settings.ini")),
            QSettings.Format.IniFormat,
        )

    def get(self, group: Dialogs, key: Union[str, Enum]):
        """
        Returns the value of a setting.
        """
        self.beginGroup(group.value, QgsSettings.Section.Plugins)

        value = self.value(key.value if isinstance(key, Enum) else key)
        if not value and DEFAULTS.get(key):
            value = DEFAULTS[key]

        self.endGroup()

        return value

    def set(self, group: Dialogs, key: Union[str, Enum], value: Any):
        """
        Set a settings value.
        """
        self.beginGroup(group.value, QgsSettings.Section.Plugins)

        self.setValue(key.value if isinstance(key, Enum) else key, value)

        self.endGroup()

    # don't override super().remove()
    def remove_(self, group: Dialogs, key: Any):
        self.beginGroup(group.value, QgsSettings.Section.Plugins)

        self.remove(key)

        self.endGroup()

    def is_debug(self) -> bool:
        """Lets us know if we're in debug mode"""

        return str_to_bool(self.get(Dialogs.SETTINGS, "debug"))

    def get_providers(
        self,
        router: RouterType,
    ) -> List[ProviderSetting]:
        """Returns all providers for ``router``.

        Stored as a JSON string. Legacy installations may still hold a
        ``PyQt_PyObject`` Variant list (pickled ``ProviderSetting``s) — PyQt6
        can fail to reconvert those, raising
        ``TypeError: unable to convert a C++ 'QVariantList' instance to a
        Python object``, especially during plugin reload. We swallow that
        and return ``[]`` so the caller repopulates from defaults, which
        will then be persisted as JSON.
        """
        self.beginGroup(Dialogs.PROVIDERS.value, QgsSettings.Section.Plugins)
        try:
            raw = self.value(router.lower())
        except TypeError:
            raw = None
        finally:
            self.endGroup()

        if not raw:
            return []
        if isinstance(raw, str):
            try:
                return [ProviderSetting(**item) for item in json.loads(raw)]
            except (json.JSONDecodeError, TypeError):
                return []
        if isinstance(raw, list):
            return [p for p in raw if isinstance(p, ProviderSetting)]
        return []

    def _set_providers(self, router: RouterType, providers: List[ProviderSetting]) -> None:
        self.set(
            Dialogs.PROVIDERS,
            router.lower(),
            json.dumps([asdict(p) for p in providers]),
        )

    def set_provider(self, router: RouterType, provider: ProviderSetting):
        existing = self.get_providers(router)
        existing.append(provider)
        self._set_providers(router, existing)

    def remove_provider(self, router: RouterType, provider_name: str):
        current = self.get_providers(router)
        self._set_providers(router, [p for p in current if p.name != provider_name])

    def pop_providers(self, router: RouterType) -> List[ProviderSetting]:
        current = self.get_providers(router)
        self.remove_(Dialogs.PROVIDERS, router.lower())

        return current

    def get_binary_dir(self) -> Optional[Path]:
        """
        Returns the path to the Valhalla binaries.
        """
        binary_dir = self.get(Dialogs.SETTINGS, "binary_dir")
        return Path(binary_dir) if binary_dir else None

    def set_binary_dir(self, binary_dir: Union[Path, str]):
        """
        Sets the path to the Valhalla binaries.
        """
        self.set(
            Dialogs.SETTINGS,
            "binary_dir",
            str(binary_dir.resolve()) if isinstance(binary_dir, Path) else binary_dir,
        )

    def get_graph_dir(self) -> Path:
        """The graph library dir — the sole source of truth for local/RE graphs.
        Each graph is a self-contained subdir ``<graph_dir>/<name>/`` (id.json +
        its data). User-relocatable; defaults under the profile."""
        raw = self.get(Dialogs.SETTINGS, "graph_dir")
        return Path(raw) if raw else get_settings_dir().joinpath("graph_dir")

    def set_graph_dir(self, graph_dir: Union[Path, str]):
        self.set(
            Dialogs.SETTINGS,
            "graph_dir",
            str(graph_dir.resolve()) if isinstance(graph_dir, Path) else graph_dir,
        )

    def get_re_authcfg(self) -> str:
        """The QGIS auth database config id holding the routing.earth API key."""
        return self.get(Dialogs.SETTINGS, "re_authcfg") or ""

    def set_re_authcfg(self, authcfg: str):
        self.set(Dialogs.SETTINGS, "re_authcfg", authcfg)

    def get_re_api_url(self) -> str:
        return self.get(Dialogs.SETTINGS, "re_api_url") or RE_API_URL_DEFAULT

    def set_re_api_url(self, url: str):
        self.set(Dialogs.SETTINGS, "re_api_url", url)

    def get_re_splitter_state(self) -> bytes:
        return self.get(Dialogs.SETTINGS, "re_splitter_state")

    def set_re_splitter_state(self, state: bytes):
        self.set(Dialogs.SETTINGS, "re_splitter_state", state)

    # TODO: remove once routing-earth-utils is installed from PyPI by the
    #   plugin itself (like pyvalhalla) — then the subprocess python is known
    #   and this dev-only escape hatch goes away
    def get_re_python(self) -> str:
        """
        The python exe running the routing-earth-utils CLI subprocess. Inside
        QGIS, PATH is not the user's shell PATH — a bare "python3" (the
        default) may resolve to a python without routing-earth-utils
        installed; this setting points at the right one (e.g. a venv's).
        """
        return self.get(Dialogs.SETTINGS, "re_python") or str(PYTHON_EXE)

    def set_re_python(self, python_exe: str):
        self.set(Dialogs.SETTINGS, "re_python", python_exe)
