"""
Deals with everything python interpreter/pip related to install extra packages.

Note: we install and run all commands with the same python version! Any package
installation will land in the QGIS settings dir. We only run bundled executables,
so we don't really care which python version QGIS is running/embedding. We just
need to find one that's actually > 3.12 for all the abi3 wheels we rely on.
"""

import importlib.metadata
import json
import os
import stat
import subprocess
import sys
import sysconfig
import zipfile
from collections import namedtuple
from enum import Enum
from functools import lru_cache
from pathlib import Path
from shutil import rmtree, which
from tempfile import TemporaryDirectory
from typing import List, Optional, Sequence, Tuple

# packaging is a hard dependency of QGIS, always available
from packaging.utils import canonicalize_name
from packaging.version import Version
from packaging.version import parse as parse_version
from qgis.core import QgsNetworkAccessManager, QgsNetworkReplyContent
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from ..exceptions import PyPiError
from ..utils.resource_utils import check_valhalla_installation
from .routing_earth import pyvalhalla_root_dir, re_utils_root_dir
from .settings import ValhallaSettings

# json_url = the PyPI JSON endpoint used for the version check
PyPiPkg = namedtuple("PyPiPkg", ("import_name", "pypi_name", "url", "json_url"))
PYVALHALLA_PKG = PyPiPkg(
    "valhalla",
    "pyvalhalla",
    "https://pypi.org/project/pyvalhalla",
    "https://pypi.org/pypi/pyvalhalla/json",
)
RE_UTILS_PKG = PyPiPkg(
    "routing_earth_utils",
    "routing-earth-utils",
    "https://pypi.org/project/routing-earth-utils",
    "https://pypi.org/pypi/routing-earth-utils/json",
)
PYPI_PKGS = (PYVALHALLA_PKG, RE_UTILS_PKG)

IS_WIN = os.name == "nt"

# our wheels (pyvalhalla, routing-earth-utils) are cp312-abi3, so nothing older can
# install them. abi3 also means anything >= this works — no exact match to the host
# needed, which is why we may pick an interpreter QGIS itself doesn't use.
_PY_FLOOR = (3, 12)
# newest first, so a $PATH search prefers a modern python over a barely-passing one
_PY_MINORS = range(20, _PY_FLOOR[1], -1)


class PyPiState(Enum):
    NOT_INSTALLED = 0
    UPGRADEABLE = 1
    UP_TO_DATE = 2


# asked of the *candidate*, not of sys: it's a different interpreter than ours
_VERSION_CMD = "import sys; print(*sys.version_info[:2])"

# the first fallback for a python without direct pip installed
# note, for completeness sake: some linux distros repackage ensurepip and put the
# path to WHEEL_PKG_DIR
_ENSUREPIP_CMD = (
    "import ensurepip, pathlib, sysconfig;"
    "d = sysconfig.get_config_var('WHEEL_PKG_DIR');"
    "dirs = ([pathlib.Path(d)] if d else [])"
    " + [pathlib.Path(ensurepip.__file__).parent / '_bundled'];"
    "print(*sorted(str(w) for p in dirs for w in p.glob('pip-*.whl')), sep='\\n')"
)
_CMD_TIMEOUT = 20
_LOG_HINT = "see the log panel for the full output"


def run_cmd(argv: Sequence, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    """
    Runs argv and returns the completed process.

    :param argv: the full argv as list of
    :param timeout: seconds before the child is killed, None for no limit
    :raises PyPiError: on a non-zero exit, a timeout or a failure to start
    """
    argv = [str(a) for a in argv]
    program = Path(argv[0]).name

    try:
        return subprocess.run(  # nosec B603
            argv,
            text=True,
            check=True,
            capture_output=True,
            timeout=timeout,
            # keeps win from flashing a console window
            **({} if not IS_WIN else {"creationflags": subprocess.CREATE_NO_WINDOW}),
        )
    except subprocess.CalledProcessError as e:
        raise PyPiError(
            f"{program} failed with exit code {e.returncode}, {_LOG_HINT}",
            detail=f"{' '.join(argv)}\n\n{e.stdout or ''}{e.stderr or ''}",
        )
    except subprocess.TimeoutExpired:
        raise PyPiError(f"{program} timed out after {timeout} s", detail=" ".join(argv))
    except OSError as e:
        raise PyPiError(f"Couldn't run {program}: {e}", detail=f"{' '.join(argv)}\n\n{e}")


def _run_cmd(argv: Sequence) -> Optional[str]:
    """internal one which doesn't raise"""
    try:
        return run_cmd(argv, timeout=_CMD_TIMEOUT).stdout.strip()
    except PyPiError:
        return None


# interpreter discovery


def _get_python_candidates() -> List[Path]:
    """Tries to find all python interpreters in best-first manner"""
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    py_exe_names = (
        [f"python{py_ver}.exe", "python.exe"] if IS_WIN else [f"python{py_ver}", "python3", "python"]
    )

    candidates: List[Path] = []

    # on mac/win, sys.executable is the QGIS interpreter, which is not what we want
    if sys.executable and Path(sys.executable).stem.lower().startswith("python"):
        candidates.append(Path(sys.executable))

    # look at the configured bin dirs
    bin_dirs = [Path(sys.base_prefix)] if IS_WIN else [Path(sys.base_prefix, "bin")]
    if bindir := sysconfig.get_config_var("BINDIR"):
        bin_dirs.append(Path(bindir))
    candidates += [d.joinpath(n) for d in bin_dirs for n in py_exe_names]

    # last resort: check PATH if there's any python3.x
    ext = ".exe" if IS_WIN else ""
    path_names = py_exe_names + [f"python3.{m}{ext}" for m in _PY_MINORS]
    candidates += [Path(p) for p in (which(n) for n in path_names) if p]

    return candidates


@lru_cache(maxsize=1)
def _resolve_python() -> Tuple[Path, Tuple[int, int]]:
    """
    Returns the first python interpreter that is new enough to install & run our abi3 wheels.
    """
    tried: List[Path] = []
    too_old: List[str] = []
    for candidate in _get_python_candidates():
        # skip dup paths
        if candidate in tried:
            continue
        tried.append(candidate)

        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue

        # get the actual version tuple and return if it's new enough
        if cand_version := _run_cmd([candidate, "-c", _VERSION_CMD]):
            try:
                major, minor = cand_version.split()[:2]
                version = (int(major), int(minor))
            except ValueError:
                continue

            if version < _PY_FLOOR:
                too_old.append(f"{candidate} is {major}.{minor}")
                continue

            return candidate.resolve(), version

    floor = ".".join(map(str, _PY_FLOOR))
    if too_old:
        raise PyPiError(
            f"Found only Python interpreters older than {floor}, which can't be used",
            detail=(
                f"Our wheels are cp{_PY_FLOOR[0]}{_PY_FLOOR[1]}-abi3 and need Python {floor} or "
                f"newer:\n" + "\n".join(too_old) + "\n\nInstalling a newer Python and restarting "
                "QGIS fixes this — it does NOT have to be the interpreter QGIS itself runs on."
            ),
        )
    raise PyPiError(
        "Couldn't find the QGIS Python interpreter",
        detail="None of these could be run:\n" + "\n".join(str(c) for c in tried),
    )


def python_exe() -> Path:
    """
    The interpreter to install with and run our subprocesses under.

    :raises PyPiError: if no working interpreter was found
    """
    return _resolve_python()[0]


def python_version() -> Tuple[int, int]:
    """
    The (major, minor) of :func:`python_exe` — ask this, not ``sys.version_info``,
    for anything describing the environment we install into.

    :raises PyPiError: if no working interpreter was found
    """
    return _resolve_python()[1]


# pip


def _bundled_pip_wheel() -> Optional[Path]:
    """Get ensurepip bundled wheel path if we can find one"""
    out = _run_cmd([python_exe(), "-c", _ENSUREPIP_CMD])
    wheels = [Path(line) for line in (out or "").splitlines() if line.strip()]

    return wheels[-1] if wheels else None


@lru_cache(maxsize=1)
def pip_argv() -> List[str]:
    """
    Either python_exe() pip or bundled ensurepip.

    :raises PyPiError: if pip can neither be run nor bootstrapped
    :returns: The argv to run pip with
    """
    exe = str(python_exe())
    if _run_cmd([exe, "-m", "pip", "--version"]):
        return [exe, "-m", "pip"]

    if wheel := _bundled_pip_wheel():
        # a wheel is a zip: <wheel>/pip is the package inside it
        from_wheel = [exe, str(wheel.joinpath("pip"))]
        if _run_cmd(from_wheel + ["--version"]):
            return from_wheel

    raise PyPiError(
        f"No pip available for {exe}. Install it and try again.",
        detail=("pip is needed to install pyvalhalla and routing-earth-utils."),
    )


# versions


def installed_version(pkg: PyPiPkg = PYVALHALLA_PKG) -> Optional[str]:
    """The installed version of ``pkg``, or None if it isn't installed."""
    if pkg.import_name == RE_UTILS_PKG.import_name:
        return _target_dist_version(re_utils_root_dir(), pkg.pypi_name)
    if pkg.import_name != PYVALHALLA_PKG.import_name:
        return None

    # pyvalhalla: read it off the valhalla_service binary the binary dir points at
    if not check_valhalla_installation():
        return None

    ext = ".exe" if IS_WIN else ""
    exe_path = ValhallaSettings().get_binary_dir().joinpath(f"valhalla_service{ext}")

    return _run_cmd([exe_path.absolute(), "--version"]) or None


def is_installed(pkg: PyPiPkg) -> bool:
    """Whether ``pkg`` is installed at all. Never raises — it's called from Qt
    slots that have nowhere to put an exception."""
    try:
        return installed_version(pkg) is not None
    except Exception:  # noqa: BLE001
        return False


def _target_dist_version(root: Path, dist_name: str) -> Optional[str]:
    """The installed version of a ``pip install --target``ed package, read from
    its dist-info METADATA via ``importlib.metadata`` (scans ``root`` only, never
    imports the package). None if not installed."""
    wanted = canonicalize_name(dist_name)
    try:
        for dist in importlib.metadata.distributions(path=[str(root)]):
            if canonicalize_name(dist.name) == wanted:
                return dist.version
    except OSError:
        return None

    return None


def pypi_version(pkg: PyPiPkg) -> Optional[Version]:
    """
    The latest version of ``pkg`` on PyPI, or **None** when we couldn't find out
    (offline, timeout, HTTP error, garbage response). Never raises: not knowing
    what's available is not an error, it just means we can't offer an upgrade.
    """
    req = QNetworkRequest(QUrl(pkg.json_url))
    req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
    try:
        res: QgsNetworkReplyContent = QgsNetworkAccessManager.instance().blockingGet(req)
        if res.error() != QNetworkReply.NetworkError.NoError:
            return None
        if res.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200:
            return None

        return parse_version(json.loads(bytes(res.content()))["info"]["version"])
    except Exception:  # noqa: BLE001 - a version check may never break a caller
        return None


def compare(local: Optional[str], available: Optional[Version]) -> PyPiState:
    """
    The state for an already known pair of versions — for callers that have
    both in hand (and want them looked up exactly once).

    An unknown ``available`` (None) never reports UPGRADEABLE: we can't claim
    there's something newer if we couldn't ask.
    """
    if local is None:
        return PyPiState.NOT_INSTALLED
    if available is not None and parse_version(local) < available:
        return PyPiState.UPGRADEABLE

    return PyPiState.UP_TO_DATE


def check_state(pkg: PyPiPkg = PYVALHALLA_PKG, available: Optional[Version] = None) -> PyPiState:
    """The installed state of ``pkg`` vs ``available`` (usually the PyPI version)."""
    return compare(installed_version(pkg), available)


# installing


def install(pkg: PyPiPkg, installed_state: PyPiState):
    """
    Installs/upgrades ``pkg`` into the plugin's profile dir.

    :param installed_state: decides if we want to do nothing, upgrade or install
    :raises PyPiError: on any failure, with the full output in ``detail``
    """
    if installed_state == PyPiState.UP_TO_DATE:
        return

    if pkg.import_name == RE_UTILS_PKG.import_name:
        _install_re_utils(installed_state)
    elif pkg.import_name == PYVALHALLA_PKG.import_name:
        _install_pyvalhalla(installed_state)
    else:
        raise PyPiError(f"Don't know how to install {pkg.pypi_name}")


def _install_pyvalhalla(installed_state: PyPiState):
    """
    pyvalhalla is a self-contained wheel: pip only downloads it, we unzip it
    ourselves (it must not land on any sys.path, see core/routing_earth.py).
    """
    pyvalhalla_dir = pyvalhalla_root_dir()
    bin_dir = pyvalhalla_dir.joinpath("valhalla", "bin")

    if installed_state == PyPiState.UPGRADEABLE:
        _rmtree(pyvalhalla_dir)
        pyvalhalla_dir.mkdir(parents=True, exist_ok=True)

    # if we got here, we'll download the latest
    with TemporaryDirectory() as temp_dir:
        run_cmd(
            pip_argv()
            + ["download", "--only-binary=:all:", "--dest", temp_dir, PYVALHALLA_PKG.pypi_name]
        )

        wheels = sorted(Path(temp_dir).glob("*.whl"))
        if not wheels:
            raise PyPiError(
                "pip downloaded no pyvalhalla wheel for this platform",
                detail=f"Nothing matching *.whl in {temp_dir} for Python {'.'.join(map(str, python_version()))}",
            )

        # unzip it to final dir
        try:
            with zipfile.ZipFile(wheels[0], "r") as zip:
                zip.extractall(pyvalhalla_dir)
        except (zipfile.BadZipFile, OSError) as e:
            raise PyPiError(f"Couldn't unpack the pyvalhalla wheel: {e}", detail=f"{wheels[0]}\n\n{e}")

        # set the execution bits
        try:
            for exe_path in bin_dir.iterdir():
                st = os.stat(exe_path)
                os.chmod(exe_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError as e:
            raise PyPiError(f"Couldn't make the valhalla binaries executable: {e}", detail=str(e))


def _install_re_utils(installed_state: PyPiState):
    """
    Unlike pyvalhalla, routing-earth-utils has dependencies, so it's a real
    ``pip install --target`` into the profile. re-utils itself goes in
    ``--no-deps`` to reuse the pyvalhalla the plugin already unpacks; only
    cryptography (+ backports.zstd before 3.14, where it's stdlib) is added.
    The ``re`` subprocess picks the dir up on its PYTHONPATH (re_process_env).
    """
    re_dir = re_utils_root_dir()
    if installed_state == PyPiState.UPGRADEABLE:
        _rmtree(re_dir)
    try:
        re_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise PyPiError(f"Couldn't create {re_dir}: {e}", detail=str(e))

    # need to ask the python interpreter which installed the bindings
    deps = ["cryptography"] if python_version() >= (3, 14) else ["cryptography", "backports.zstd"]
    target = ["install", "--target", str(re_dir)]
    run_cmd(pip_argv() + target + ["--no-deps", RE_UTILS_PKG.pypi_name])
    run_cmd(pip_argv() + target + deps)


def _rmtree(path: Path):
    """:raises PyPiError: e.g. on Windows when the valhalla server still holds a file"""
    try:
        rmtree(path, ignore_errors=False)
    except FileNotFoundError:
        return
    except OSError as e:
        raise PyPiError(
            f"Couldn't remove {path}. Is the local Valhalla server still running?",
            detail=str(e),
        )
