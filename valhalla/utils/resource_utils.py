import importlib.util
import json
import os
from enum import Enum
from pathlib import Path

from qgis.core import QgsNetworkReplyContent
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from .. import RESOURCE_PATH
from ..core.settings import ValhallaSettings, get_settings_dir
from ..third_party.routingpy.routingpy import exceptions

IS_WIN = os.name == "nt"


class ResGroups(Enum):
    ICONS = "icons"
    UI = "ui"


def get_icon(filename: str) -> QIcon:
    """Returns a QIcon either from the theme or from resources"""
    return (
        QIcon(str(get_resource_path(ResGroups.ICONS.value, filename)))
        if not filename.startswith(":")
        else QIcon.fromTheme(filename)
    )


def get_resource_path(*args) -> Path:
    """All args are interpreted as string"""
    return RESOURCE_PATH.joinpath(*args)


def get_json_body(response: QgsNetworkReplyContent) -> dict:
    """
    Parse the response and return the JSON body.

    :param response: The full response
    :raises routingpy.exceptions.Timeout: On server timeout
    :raises routingpy.exceptions.RouterError: If there's no HTTP status code
    :raises routingpy.exceptions.JSONParseError: If it's not a JSON response
    :raises routingpy.exceptions.OverQueryLimit: On 429 HTTP error
    :raises routingpy.exceptions.RouterApiError: On 400 - 499 HTTP error
    :raises routingpy.exceptions.RouterServerError: On > 500 HTTP error
    """

    error_code = response.error()

    if error_code == QNetworkReply.NetworkError.TimeoutError:
        raise exceptions.Timeout("Request timed out.")

    status_code = response.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    if status_code is None:
        msg = f"{response.errorString()} for URL {response.request().url().toString()}"
        raise exceptions.RouterError(response.error(), msg)

    raw = bytes(response.content())
    try:
        body = json.loads(raw or b"{}")
    except json.decoder.JSONDecodeError:
        # non-JSON body (e.g. upstream returned plain text like "The server didn't
        # respond in time"). Only treat this as a parse error on 200 OK; otherwise
        # surface the raw text via the appropriate RouterError subclass.
        if status_code == 200:
            raise exceptions.JSONParseError(f"Can't decode JSON response: {raw!r}")
        body = raw.decode(errors="replace").strip() or response.errorString()

    if status_code == 429:
        raise exceptions.OverQueryLimit(status_code, body)
    elif 400 <= status_code < 500:
        raise exceptions.RouterApiError(status_code, body)
    elif 500 <= status_code:
        raise exceptions.RouterServerError(status_code, body)

    if status_code != 200:
        raise exceptions.RouterError(status_code, body)

    return body


def get_valhalla_config_path():
    return get_settings_dir().joinpath("valhalla.json")


def create_valhalla_config(force=False):
    config_path = get_valhalla_config_path()
    if config_path.exists() and not force:
        return

    # load the config builder from
    module_path = ValhallaSettings().get_binary_dir().parent.joinpath("valhalla_build_config.py")
    # try to find it in the binary dir directly (e.g. on unix source builds), else raise
    if not module_path.exists():
        module_path = ValhallaSettings().get_binary_dir().joinpath("valhalla_build_config.py")
        if not module_path.exists():
            raise ModuleNotFoundError("Can't find valhalla_build_config.py (provided by pyvalhalla)")

    spec = importlib.util.spec_from_file_location("valhalla_build_config", module_path)
    valhalla_build_config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(valhalla_build_config)

    def _sanitize_config(dict_: dict = None) -> dict:
        """remove the "Optional" values from the config."""
        int_dict_ = dict_.copy()
        for k, v in int_dict_.items():
            if isinstance(v, valhalla_build_config.Optional):
                del dict_[k]
            elif isinstance(v, dict):
                _sanitize_config(v)

        return dict_

    # need to remove the items we store in each graph folder's 'id.json'
    config = _sanitize_config(valhalla_build_config.config)

    del config["mjolnir"]["tile_dir"]
    del config["mjolnir"]["tile_extract"]
    try:
        del config["mjolnir"]["tile_url"]
        del config["mjolnir"]["tile_url_user_pw"]
        del config["loki"]["use_connectivity"]
    except KeyError:
        pass

    # allow verbose status for bbox
    config["service_limits"]["status"]["allow_verbose"] = True

    with config_path.open("w") as f:
        json.dump(config, f, indent=2)


def check_valhalla_installation() -> bool:
    current_bin_dir = ValhallaSettings().get_binary_dir()
    program = "valhalla_service" if not IS_WIN else "valhalla_service.exe"

    if current_bin_dir is None:
        return False
    elif not current_bin_dir.exists():
        return False
    elif (valhalla_exe := current_bin_dir.joinpath(program)).exists():
        if not valhalla_exe.is_file():
            return False

        if IS_WIN:
            pathext = os.environ.get("PATHEXT", "")
            return valhalla_exe.suffix.lower() in (ext.lower() for ext in pathext.split(";"))
        else:
            return os.access(valhalla_exe, os.X_OK)

    return False


def get_default_valhalla_binary_dir() -> Path:
    return get_settings_dir().joinpath("pyvalhalla", "valhalla", "bin")
