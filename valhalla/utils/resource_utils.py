import importlib.metadata
import importlib.util
import json
import os
import platform
import shlex
import stat
import subprocess
import sys
import zipfile
from enum import Enum
from pathlib import Path
from shutil import rmtree
from tempfile import TemporaryDirectory
from typing import Optional

from packaging.utils import canonicalize_name
from packaging.version import Version
from packaging.version import parse as parse_version
from qgis.core import QgsNetworkAccessManager, QgsNetworkReplyContent
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from .. import RESOURCE_PATH
from ..core.routing_earth import re_utils_root_dir
from ..core.settings import ValhallaSettings, get_settings_dir
from ..exceptions import ValhallaCmdError
from ..global_definitions import PYTHON_EXE, PYVALHALLA_PKG, RE_UTILS_PKG, PyPiPkg, PyPiState
from ..third_party.routingpy.routingpy import exceptions

# test.pypi doesn't host re-utils' deps (cryptography, pyvalhalla) — pull the
# package from there but let its deps resolve from real PyPI
TESTPYPI_INDEX_ARGS = (
    "--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/"
)


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


def check_local_lib_version(available_version: Version, pkg: PyPiPkg = PYVALHALLA_PKG) -> PyPiState:
    """
    The installed state of ``pkg`` vs the version we expect (usually the current
    PyPI version).
    """

    local_version = get_local_lib_version(pkg)
    if local_version is None:
        return PyPiState.NOT_INSTALLED
    if parse_version(local_version) < available_version:
        return PyPiState.UPGRADEABLE

    return PyPiState.UP_TO_DATE


def get_local_lib_version(pkg: PyPiPkg = PYVALHALLA_PKG) -> Optional[str]:
    if pkg.import_name == RE_UTILS_PKG.import_name:
        return _target_dist_version(re_utils_root_dir(), pkg.pypi_name)

    # pyvalhalla: read it off the bundled valhalla_service binary
    if not check_valhalla_installation():
        return None
    try:
        exe_path = ValhallaSettings().get_binary_dir().joinpath("valhalla_service")
        proc: subprocess.CompletedProcess = exec_cmd(f"{exe_path.absolute()} --version")
    except (ValhallaCmdError, subprocess.CalledProcessError):
        return None

    stdout = proc.stdout.split()
    if not len(stdout):
        return None

    # currently valhalla_service -v prints also the program name
    # see https://github.com/valhalla/valhalla/pull/5769/
    return stdout[1] if len(stdout) == 2 else stdout[0]


def _target_dist_version(root: Path, dist_name: str) -> Optional[str]:
    """The installed version of a ``pip install --target``ed package, read from
    its dist-info METADATA via ``importlib.metadata`` (scans ``root`` only, never
    imports the package). None if not installed."""
    wanted = canonicalize_name(dist_name)
    for dist in importlib.metadata.distributions(path=[str(root)]):
        if canonicalize_name(dist.name) == wanted:
            return dist.version
    return None


def get_pypi_lib_version(pypi_pkg: PyPiPkg) -> Version:
    nam = QgsNetworkAccessManager.instance()
    url = QUrl(pypi_pkg.json_url)
    req = QNetworkRequest(url)
    req.setHeader(
        QNetworkRequest.KnownHeaders.ContentTypeHeader,
        "application/json",
    )

    res: QgsNetworkReplyContent = nam.blockingGet(req)
    try:
        v = get_json_body(res)["info"]["version"]
    except exceptions.RouterError:
        v = "0.0.0"

    return Version(v)


def install_pyvalhalla(installed_state: PyPiState):
    """
    Installs/upgrade packages from PyPI.

    :param installed_state: decides if we want to do nothing, upgrade or install
    :raises ValhallaCmdError: when exit code other than 0
    """
    if installed_state == PyPiState.UP_TO_DATE:
        return

    bin_dir = get_default_valhalla_binary_dir()
    pyvalhalla_dir = bin_dir.parent.parent

    if installed_state == PyPiState.UPGRADEABLE:
        rmtree(pyvalhalla_dir)
        pyvalhalla_dir.mkdir(parents=True, exist_ok=False)

    # if we got here, we'll download the latest
    with TemporaryDirectory() as temp_dir:
        # download wheel to temp dir
        try:
            exec_cmd(f"{PYTHON_EXE} -m pip download --only-binary=:all: --dest {temp_dir} pyvalhalla")
        except subprocess.CalledProcessError as e:
            raise ValhallaCmdError(f"Couldnt install pyvalhalla: {e.stderr}")

        # unzip it to final dir
        wheel_path = Path(temp_dir, os.listdir(temp_dir)[0])
        with zipfile.ZipFile(wheel_path, "r") as zip:
            zip.extractall(pyvalhalla_dir)

        # set the execution bits
        for exe_path in bin_dir.iterdir():
            st = os.stat(exe_path)
            os.chmod(exe_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_routing_earth_utils(installed_state: PyPiState):
    """
    Installs/upgrades routing-earth-utils + its deps into the profile via
    ``pip install --target`` (unlike pyvalhalla it isn't self-contained). The
    ``re`` subprocess picks the dir up on its PYTHONPATH (see re_process_env).
    re-utils itself is pulled ``--no-deps`` so it reuses the pyvalhalla the
    plugin already unpacks; only cryptography (+ backports.zstd on <3.14) is
    added here.

    :raises ValhallaCmdError: when a pip invocation fails
    """
    if installed_state == PyPiState.UP_TO_DATE:
        return

    re_dir = re_utils_root_dir()
    if installed_state == PyPiState.UPGRADEABLE:
        rmtree(re_dir, ignore_errors=True)
    re_dir.mkdir(parents=True, exist_ok=True)

    # backports.zstd is stdlib from 3.14; same interpreter installs and runs,
    # so sys.version_info matches PYTHON_EXE
    deps = "cryptography" if sys.version_info >= (3, 14) else "cryptography backports.zstd"
    try:
        exec_cmd(
            f'"{PYTHON_EXE}" -m pip install --target "{re_dir}" --no-deps '
            f"{RE_UTILS_PKG.pypi_name} {TESTPYPI_INDEX_ARGS}"
        )
        exec_cmd(f'"{PYTHON_EXE}" -m pip install --target "{re_dir}" {deps}')
    except subprocess.CalledProcessError as e:
        raise ValhallaCmdError(f"Couldn't install routing-earth-utils: {e.stderr}")


def install_pkg(pkg: PyPiPkg, installed_state: PyPiState):
    """Dispatches to the right installer for a deps-table row."""
    if pkg.import_name == RE_UTILS_PKG.import_name:
        install_routing_earth_utils(installed_state)
    else:
        install_pyvalhalla(installed_state)


def exec_cmd(cmd: str) -> subprocess.CompletedProcess:
    """
    Executes a command and returns the Process. stdout/stderr are strings.

    :param cmd: the full command to be executed
    :returns: the completed (success or failure) process instance
    """
    is_win = platform.system() == "Windows"
    cmd_split = shlex.split(cmd, posix=not is_win)

    return subprocess.run(cmd_split, text=True, check=True, capture_output=True, shell=False)


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

    if current_bin_dir is None:
        return False
    elif not current_bin_dir.exists():
        return False
    elif (
        valhalla_exe := current_bin_dir.joinpath(
            ("valhalla_service" if os.name != "nt" else "valhalla_service.exe")
        )
    ).exists():
        if not valhalla_exe.is_file():
            return False

        if os.name == "nt":
            pathext = os.environ.get("PATHEXT", "")
            return valhalla_exe.suffix.lower() in (ext.lower() for ext in pathext.split(";"))
        else:
            return os.access(valhalla_exe, os.X_OK)

    return False


def get_default_valhalla_binary_dir() -> Path:
    return get_settings_dir().joinpath("pyvalhalla", "valhalla", "bin")
