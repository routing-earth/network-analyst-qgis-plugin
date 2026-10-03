import json
import os
import unittest
from dataclasses import asdict

from tests.utilities import get_qgis_app

QGIS_APP, CANVAS, IFACE, PARENT = get_qgis_app()

from valhalla.core import pypi, spopt  # noqa: E402
from valhalla.core.spopt import SolveRequest, SolveResponse, SpoptProblem  # noqa: E402
from valhalla.exceptions import SpoptError  # noqa: E402

# clients x facilities, radius 10: fac 0 covers clients 0+1, fac 1 covers 2+3, fac 2 covers 1+2.
# Clients 0 and 3 are only reachable from fac 0 and fac 1 respectively.
RADIUS = 10
MATRIX = [
    [5, 50, 50],
    [5, 50, 5],
    [50, 5, 5],
    [50, 5, 50],
]


class TestSpopt(unittest.TestCase):
    """Runs the real runner subprocess, needs no valhalla, installs spopt if missing."""

    @classmethod
    def setUpClass(cls):
        if not pypi.is_installed(pypi.SPOPT_PKG):
            pypi.install(pypi.SPOPT_PKG, pypi.PyPiState.NOT_INSTALLED)

    def test_lscp(self):
        fac2cli = spopt.solve(SpoptProblem.LSCP, MATRIX, RADIUS)
        self.assertEqual(fac2cli, [[0, 1], [2, 3], None])

    def test_lscp_predefined(self):
        fac2cli = spopt.solve(SpoptProblem.LSCP, MATRIX, RADIUS, predefined=[0, 0, 1])
        self.assertEqual(fac2cli, [[0, 1], [2, 3], [1, 2]])

    def test_mclp(self):
        # one facility: fac 1 covers the heavy client 3
        fac2cli = spopt.solve(SpoptProblem.MCLP, MATRIX, RADIUS, p_facilities=1, weights=[1, 1, 1, 10])
        self.assertEqual(fac2cli, [None, [2, 3], None])

    def test_selected_without_clients(self):
        """A selected facility which covers nobody is still selected, not dropped."""
        matrix = [[5, 50], [5, 50]]  # fac 0 covers both clients, fac 1 nobody
        self.assertEqual(spopt.solve(SpoptProblem.LSCP, matrix, RADIUS, predefined=[0, 1]), [[0, 1], []])
        self.assertEqual(
            spopt.solve(SpoptProblem.MCLP, matrix, RADIUS, p_facilities=2, weights=[1, 1]), [[0, 1], []]
        )
        self.assertEqual(spopt.solve(SpoptProblem.LSCP, matrix, RADIUS), [[0, 1], None])

    def test_infeasible(self):
        # client 3 can't be reached from anywhere
        matrix = [row[:] for row in MATRIX]
        matrix[3] = [50, 50, 50]
        with self.assertRaisesRegex(SpoptError, "Infeasible"):
            spopt.solve(SpoptProblem.LSCP, matrix, RADIUS)

    def test_bad_input(self):
        with self.assertRaisesRegex(SpoptError, "empty"):
            spopt.solve(SpoptProblem.LSCP, [], RADIUS)
        with self.assertRaisesRegex(SpoptError, "p_facilities"):
            spopt.solve(SpoptProblem.MCLP, MATRIX, RADIUS, weights=[1, 1, 1, 1])

    def test_protocol_mismatch(self):
        """An unknown or missing request key fails loudly instead of being ignored."""
        env = {**os.environ, "PYTHONPATH": str(pypi.spopt_root_dir())}
        request = asdict(SolveRequest(problem="lscp", cost_matrix=MATRIX, service_radius=RADIUS))
        for bad in ({**request, "bogus": 1}, {k: v for k, v in request.items() if k != "cost_matrix"}):
            proc = pypi.run_python([spopt.RUNNER], stdin=json.dumps(bad), env=env)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("SolveRequest", SolveResponse(**json.loads(proc.stdout)).error)

    def test_cancel(self):
        self.assertIsNone(spopt.solve(SpoptProblem.LSCP, MATRIX, RADIUS, is_canceled=lambda: True))

    def test_not_installed(self):
        spopt.pypi.is_installed, real = lambda pkg: False, pypi.is_installed
        try:
            with self.assertRaisesRegex(SpoptError, "not installed"):
                spopt.solve(SpoptProblem.LSCP, MATRIX, RADIUS)
        finally:
            spopt.pypi.is_installed = real
