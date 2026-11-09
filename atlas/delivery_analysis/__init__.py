"""Ограниченный offline-прототип вариативной поставки на синтетических данных."""

from .model import DeliveryAnalysisError, DeliveryProblem, dependency_components, prepare_problem
from .evaluation import evaluate_delivery
from .search import baseline_assignments, compare_search, exact_partition_oracle, genetic_search

__all__ = ['DeliveryAnalysisError', 'DeliveryProblem', 'prepare_problem',
           'dependency_components', 'evaluate_delivery', 'baseline_assignments',
           'exact_partition_oracle', 'genetic_search', 'compare_search']
