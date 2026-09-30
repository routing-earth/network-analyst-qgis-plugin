"""
Location-allocation via pysal/spopt, solved out of process: runner.py runs under
pypi.python_exe() with only the spopt tree (pypi.spopt_root_dir()) on PYTHONPATH.

Never import spopt in-process: its dependency tree bundles its own PROJ/GEOS/GDAL next
to the ones QGIS already loaded, would clash with the host numpy in sys.modules, and
a minutes-long solve couldn't be killed.
"""

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from ...exceptions import PyPiError, SpoptError
from .. import pypi

# stdlib-only at module level, safe to import in-process: the protocol lives there
from .runner import SolveRequest, SolveResponse, SpoptProblem

RUNNER = Path(__file__).parent.joinpath("runner.py")


def solve(
    problem: SpoptProblem,
    cost_matrix: Sequence[Sequence[float]],
    service_radius: float,
    p_facilities: Optional[int] = None,
    weights: Optional[Sequence[float]] = None,
    predefined: Optional[Sequence[int]] = None,
    is_canceled: Callable[[], bool] = lambda: False,
) -> Optional[List[Optional[List[int]]]]:
    """
    Solves ``problem`` and returns, per facility (matrix column), the indices of the
    clients (matrix rows) it covers, or None for a facility that wasn't selected. A selected
    facility may cover nobody, and coverage problems may assign a client to several facilities.

    :param cost_matrix: clients x facilities
    :param p_facilities: MCLP only
    :param weights: MCLP only, per client
    :param predefined: per facility, 1 = must be selected
    :param is_canceled: polled while solving, e.g. a QgsFeedback's isCanceled
    :returns: fac2cli, None if canceled
    :raises SpoptError: if spopt isn't installed or the solve failed
    """
    if not pypi.is_installed(pypi.SPOPT_PKG):
        raise SpoptError(
            f"{pypi.SPOPT_PKG.pypi_name} is not installed, install it in the plugin settings"
        )

    request = SolveRequest(
        problem=SpoptProblem(problem).value,
        cost_matrix=[list(map(float, row)) for row in cost_matrix],
        service_radius=float(service_radius),
        p_facilities=p_facilities,
        weights=list(weights) if weights is not None else None,
        predefined=list(predefined) if predefined is not None else None,
    )

    # only the spopt tree, not the host's PYTHONPATH: nothing from the QGIS env may mix in
    env = {**os.environ, "PYTHONPATH": str(pypi.spopt_root_dir())}
    try:
        proc = pypi.run_python(
            [RUNNER], stdin=json.dumps(asdict(request)), env=env, is_canceled=is_canceled
        )
    except PyPiError as e:
        raise SpoptError(str(e), detail=e.detail)

    if proc is None:
        return None

    detail = f"{' '.join(proc.args)}\n\nexit code {proc.returncode}\n\n{proc.stderr}"
    try:
        response = SolveResponse(**json.loads(proc.stdout))
    except (ValueError, TypeError):
        # the runner died before it could answer, e.g. a crash in a native lib
        raise SpoptError(f"The spopt solver crashed with exit code {proc.returncode}", detail=detail)

    if proc.returncode != 0 or response.error or response.fac2cli is None:
        raise SpoptError(f"spopt failed: {response.error or 'unknown error'}", detail=detail)

    return response.fac2cli
