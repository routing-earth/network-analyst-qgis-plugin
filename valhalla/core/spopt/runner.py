"""
Solves one spopt location problem on a cost matrix. Runs as a subprocess of the plugin
(see core/spopt/__init__.py), under pypi.python_exe() with the spopt tree on PYTHONPATH.

Standalone on purpose: no plugin imports, stdlib + spopt/pulp/numpy only.

The protocol is defined once, here: stdin is a JSON SolveRequest, stdout a JSON
SolveResponse, exit 0 if solved, else 1. The client imports this module in-process
for these types, so everything but the stdlib must only be imported inside solve().
"""

import contextlib
import json
import sys
import traceback
from dataclasses import asdict, dataclass
from enum import Enum
from typing import List, Optional

# a solution is binary, but solvers return floats with noise, which spopt tests with > 0
_SELECTED = 0.5


class SpoptProblem(str, Enum):
    LSCP = "lscp"
    MCLP = "mclp"


@dataclass
class SolveRequest:
    problem: str  # a SpoptProblem value
    cost_matrix: List[List[float]]  # clients x facilities
    service_radius: float
    p_facilities: Optional[int] = None  # mclp only
    weights: Optional[List[float]] = None  # per client, mclp only
    predefined: Optional[List[int]] = None  # per facility, 1 = must be selected


@dataclass
class SolveResponse:
    status: Optional[str] = None
    # per facility: the client indices it covers (may be none), null if it wasn't selected
    fac2cli: Optional[List[Optional[List[int]]]] = None
    error: Optional[str] = None


class InputError(Exception):
    pass


def _build(req: SolveRequest, np, spopt_locate):
    cost_matrix = np.asarray(req.cost_matrix, dtype=float)
    if cost_matrix.ndim != 2 or 0 in cost_matrix.shape:
        raise InputError("The cost matrix is empty")

    predefined = np.asarray(req.predefined, dtype=int) if req.predefined is not None else None

    problem = SpoptProblem(req.problem)
    if problem == SpoptProblem.LSCP:
        return spopt_locate.LSCP.from_cost_matrix(
            cost_matrix,
            service_radius=float(req.service_radius),
            predefined_facilities_arr=predefined,
        )
    if problem == SpoptProblem.MCLP:
        if missing := [k for k in ("p_facilities", "weights") if getattr(req, k) is None]:
            raise InputError(f"MCLP needs {', '.join(missing)}")
        return spopt_locate.MCLP.from_cost_matrix(
            cost_matrix,
            weights=np.asarray(req.weights, dtype=float),
            service_radius=float(req.service_radius),
            p_facilities=int(req.p_facilities),
            predefined_facilities_arr=predefined,
        )

    raise InputError(f"Unsupported problem type '{problem.value}'")


def _fac2cli(model) -> List[Optional[List[int]]]:
    """
    Like spopt's facility_client_array(), but with a tolerant threshold and telling a facility
    which wasn't selected (None) from a selected one covering nobody ([]): MCLP sites exactly
    p facilities and a predefined one is selected wherever it is.

    A client is covered by every selected facility within the service radius (model.aij).
    """
    n_clients = model.aij.shape[0]
    return [
        [i for i in range(n_clients) if model.aij[i, j] > 0] if fac_var.value() > _SELECTED else None
        for j, fac_var in enumerate(model.fac_vars)
    ]


def solve(req: SolveRequest) -> SolveResponse:
    try:
        import numpy as np
        import pulp
        from spopt import locate as spopt_locate
    except ImportError as e:
        raise InputError(f"spopt is not installed properly: {e}")

    model = _build(req, np, spopt_locate)
    model.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    status = pulp.LpStatus[model.problem.status]
    if model.problem.status != pulp.LpStatusOptimal:
        raise InputError(f"No optimal solution: {status}")

    return SolveResponse(status=status, fac2cli=_fac2cli(model))


def main() -> int:
    stdout = sys.stdout
    try:
        # a missing or unknown key is a TypeError
        req = SolveRequest(**json.load(sys.stdin))
        # stdout is the result channel, nothing else may print to it
        with contextlib.redirect_stdout(sys.stderr):
            response = solve(req)
    except (InputError, ValueError, TypeError) as e:
        # the caller logs stderr as detail
        traceback.print_exc()
        json.dump(asdict(SolveResponse(error=str(e))), stdout)
        return 1

    json.dump(asdict(response), stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
