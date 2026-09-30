from ...core.spopt import SpoptProblem
from .base_algorithm import SpoptBaseAlgorithm
from .coverage_mixin import CoverageMixin


class LSCPAlgorithm(CoverageMixin, SpoptBaseAlgorithm):
    PROBLEM = SpoptProblem.LSCP

    def displayName(self):
        return "Location Set Covering Problem (LSCP)"

    def init_problem_params(self):
        self.init_coverage_params()

    def get_problem_kwargs(self, parameters, context, fac_ids, dem_ids, fac_feats, dem_feats):
        return self.get_coverage_kwargs(parameters, context, fac_ids, fac_feats)
