# app/eval/__init__.py
"""Evaluation Flywheel and Autorater Suite (FDE Pillar 3: SCALE).
Eliminates subjective 'vibe-checking' through continuous, objective benchmarks.
"""
from .autorater import (
    EvaluationReport,
    FinancialAutorater,
    MathReconciliationAutorater,
    ExtractionCompletenessAutorater,
    CategoryF1Autorater,
    FiduciarySoundnessAutorater,
    run_full_evaluation_suite,
)

__all__ = [
    "EvaluationReport",
    "FinancialAutorater",
    "MathReconciliationAutorater",
    "ExtractionCompletenessAutorater",
    "CategoryF1Autorater",
    "FiduciarySoundnessAutorater",
    "run_full_evaluation_suite",
]

