"""
routing-earth.com client plumbing (no UI in here): subprocess invocation of
the ``routing-earth-utils`` CLI, API-key storage in the QGIS auth database,
and the entitlements HTTP call. Registry/entry concerns live in
core/graph_registry.py (which this module may be imported alongside, never
the reverse).

The client CLI runs as a subprocess (``PYTHON_EXE -m routing_earth_utils.cli``):
inside QGIS this plugin's own package is named ``valhalla`` and shadows
pyvalhalla in ``sys.modules``, so ``routing_earth_utils`` (which imports
``valhalla.baldr``) can never be imported in-process. The plugin installs
routing-earth-utils + its deps into ``re_utils_root_dir()`` (utils/
resource_utils.py, like pyvalhalla); that dir and the pyvalhalla dir are
prepended to the subprocess PYTHONPATH so the right packages win.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsNetworkAccessManager
from qgis.PyQt.QtCore import QProcessEnvironment, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from .settings import ValhallaSettings, get_settings_dir

RE_AUTHCFG_NAME = "routing.earth"


@dataclass
class Entitlement:
    """One (scope, cadence) the API key may resolve, enriched with the newest
    full snapshot's metadata. The size/dataset fields are None for a wildcard
    scope or a scope with no available snapshot yet. Mirrors the snake_case
    ``/api/v1/entitlements`` item (and re-utils ``Entitlement.from_dict``)."""

    scope: str  # may be "*" (wildcard: any region goes)
    cadence: str
    compressed_size_bytes: Optional[int] = None  # over-the-wire download size
    size_bytes: Optional[int] = None  # extracted / on-disk size
    dataset_id: Optional[int] = None
    osm_data_timestamp: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Entitlement":
        return cls(
            scope=d["scope"],
            cadence=d["cadence"],
            compressed_size_bytes=d.get("compressed_size_bytes"),
            size_bytes=d.get("size_bytes"),
            dataset_id=d.get("dataset_id"),
            osm_data_timestamp=d.get("osm_data_timestamp"),
        )


def pyvalhalla_root_dir() -> Path:
    """The dir containing the unpacked pyvalhalla wheel (i.e. holds ``valhalla/``)."""
    return get_settings_dir().joinpath("pyvalhalla")


def re_utils_root_dir() -> Path:
    """The dir holding the pip-installed routing-earth-utils + its deps
    (prepended to the ``re`` subprocess PYTHONPATH; holds ``routing_earth_utils/``)."""
    return get_settings_dir().joinpath("routing_earth_utils")


def fetch_entitlements() -> List[Entitlement]:
    """
    Everything the stored API key may resolve, straight from the HTTP API — no
    subprocess needed, unlike the CLI ops.

    :raises ValueError: no key stored, HTTP failure or a malformed response.
    """
    api_key = get_re_api_key()
    if not api_key:
        raise ValueError("no API key stored")
    url = ValhallaSettings().get_re_api_url().rstrip("/") + "/api/v1/entitlements"
    req = QNetworkRequest(QUrl(url))
    req.setRawHeader(b"Authorization", f"Bearer {api_key}".encode())

    reply = QgsNetworkAccessManager.instance().blockingGet(req)
    if reply.error() != QNetworkReply.NetworkError.NoError:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raise ValueError(f"entitlements: {reply.errorString()} (HTTP {status})")
    try:
        entitlements = json.loads(bytes(reply.content()))["entitlements"]
        return [Entitlement.from_dict(e) for e in entitlements]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise ValueError(f"entitlements: malformed response ({e})")


def re_cli_args(cmd: str, tar_path: Path, *extra_args: str) -> List[str]:
    """The full argv (after the python exe) for one ``re`` CLI invocation."""
    return [
        "-m",
        "routing_earth_utils.cli",
        cmd,
        "--tar-extract",
        str(tar_path),
        "--api-base-url",
        ValhallaSettings().get_re_api_url(),
        "-v",
        *extra_args,
    ]


def re_process_env(api_key: str) -> QProcessEnvironment:
    """
    The subprocess environment: system env with our plugin-managed dirs
    prepended to PYTHONPATH (routing_earth_utils + its deps, then pyvalhalla —
    both must win over any ambient install) and the API key injected via env,
    never argv (visible in ps) or QSettings. Set on the child only, never the
    shell; ``os.pathsep`` keeps it Windows-correct.
    """
    env = QProcessEnvironment.systemEnvironment()
    paths = []
    if re_utils_root_dir().joinpath("routing_earth_utils").is_dir():
        paths.append(str(re_utils_root_dir()))
    if pyvalhalla_root_dir().joinpath("valhalla").is_dir():
        paths.append(str(pyvalhalla_root_dir()))
    if paths:
        if existing := env.value("PYTHONPATH"):
            paths.append(existing)
        env.insert("PYTHONPATH", os.pathsep.join(paths))
    env.insert("RE_API_KEY", api_key)

    return env


def get_re_api_key() -> Optional[str]:
    """The API key from the QGIS auth database (may prompt for the master password)."""
    authcfg = ValhallaSettings().get_re_authcfg()
    if not authcfg:
        return None
    cfg = QgsAuthMethodConfig()
    if not QgsApplication.authManager().loadAuthenticationConfig(authcfg, cfg, True):
        return None

    return cfg.configMap().get("Authorization", "").replace("Bearer ", "") or None


def set_re_api_key(key: str) -> bool:
    """Stores/updates the API key as an APIHeader config in the QGIS auth database."""
    auth_manager = QgsApplication.authManager()
    authcfg = ValhallaSettings().get_re_authcfg()

    cfg = QgsAuthMethodConfig("APIHeader")
    cfg.setName(RE_AUTHCFG_NAME)
    cfg.setConfigMap({"Authorization": f"Bearer {key}"})

    if authcfg:
        cfg.setId(authcfg)
        if auth_manager.updateAuthenticationConfig(cfg):
            return True

    if not auth_manager.storeAuthenticationConfig(cfg):
        return False
    ValhallaSettings().set_re_authcfg(cfg.id())

    return True
