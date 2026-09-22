import os
import unittest
from pathlib import Path

from packaging.version import Version

from valhalla.core import pypi
from valhalla.exceptions import PyPiError

BOGUS_PKG = pypi.PyPiPkg(
    "not_a_package", "not-a-package", "https://example.invalid", "https://no.such.host.invalid/json"
)


class TestPyPi(unittest.TestCase):
    """
    Environment-independent: needs neither the docker service nor pyvalhalla,
    only the QGIS app the tests package bootstraps.
    """

    def setUp(self):
        # the resolution is memoized, so a test that stubs a probe must not leak
        pypi._resolve_python.cache_clear()
        pypi.pip_argv.cache_clear()

    tearDown = setUp

    def test_python_exe(self):
        exe = pypi.python_exe()
        self.assertIsInstance(exe, Path)
        self.assertTrue(exe.is_file())
        self.assertTrue(os.access(exe, os.X_OK))

        # it must be the interpreter it claims to be, not just a path that exists
        out = pypi.run_cmd([exe, "-c", "import sys; print(*sys.version_info[:2])"]).stdout.split()
        self.assertEqual(pypi.python_version(), (int(out[0]), int(out[1])))

    def test_python_exe_failure(self):
        pypi._run_cmd, real_probe = lambda argv: None, pypi._run_cmd
        try:
            with self.assertRaises(PyPiError):
                pypi.python_exe()
        finally:
            pypi._run_cmd = real_probe

    def test_pip_argv(self):
        argv = pypi.pip_argv()
        self.assertEqual(argv[0], str(pypi.python_exe()))
        self.assertTrue(pypi.run_cmd(argv + ["--version"]).stdout.startswith("pip "))

    def test_pip_argv_without_pip(self):
        """Without `-m pip` it falls back to ensurepip's bundled wheel."""
        real_probe = pypi._run_cmd
        pypi._run_cmd = lambda argv: None if list(argv[1:3]) == ["-m", "pip"] else real_probe(argv)
        try:
            if pypi._bundled_pip_wheel() is None:
                self.skipTest("this interpreter has no bundled ensurepip wheel (Debian & co.)")
            argv = pypi.pip_argv()
            self.assertTrue(argv[1].endswith(f".whl{os.sep}pip"))
            self.assertTrue(pypi.run_cmd(argv + ["--version"]).stdout.startswith("pip "))
        finally:
            pypi._run_cmd = real_probe

    def test_run_failures(self):
        """Nothing but PyPiError ever comes out of run()."""
        with self.assertRaises(PyPiError):  # doesn't exist
            pypi.run_cmd([Path("/no/such/python"), "-c", "pass"])
        with self.assertRaises(PyPiError):  # non-zero exit
            pypi.run_cmd([pypi.python_exe(), "-c", "import sys; sys.exit(3)"])
        with self.assertRaises(PyPiError):  # timeout
            pypi.run_cmd([pypi.python_exe(), "-c", "import time; time.sleep(30)"], timeout=1)

    def test_pypi_version_unreachable(self):
        """An unreachable index is unknown, not an error."""
        self.assertIsNone(pypi.pypi_version(BOGUS_PKG))

    def test_check_state_without_available(self):
        """We may never claim an upgrade if we couldn't ask what's available."""
        self.assertNotEqual(pypi.check_state(pypi.RE_UTILS_PKG, None), pypi.PyPiState.UPGRADEABLE)
        self.assertNotEqual(pypi.check_state(pypi.PYVALHALLA_PKG, None), pypi.PyPiState.UPGRADEABLE)

    def test_check_state_not_installed(self):
        self.assertEqual(pypi.check_state(BOGUS_PKG, Version("1.0.0")), pypi.PyPiState.NOT_INSTALLED)

    def test_install_up_to_date_is_a_noop(self):
        pypi.install(BOGUS_PKG, pypi.PyPiState.UP_TO_DATE)

    def test_is_installed(self):
        self.assertFalse(pypi.is_installed(BOGUS_PKG))
