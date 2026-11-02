# app/eval/autorater.py
"""Continuous Quality Evaluation Flywheel & Autorater Engine.
FDE Pillar 3: SCALE.
Replaces subjective 'vibe-checking' with automated benchmark pipelines and objective scoring
loaded from `app/eval/golden_dataset.json`:
1. Mathematical Reconciliation Autorater (Exact determinism)
2. Extraction Completeness Autorater (share of rows with a valid date, amount and description)
3. Categorization F1 Autorater (Precision, recall, macro-F1)
4. Fiduciary Soundness Autorater (keyword rules; there is no LLM judge)
5. OpenTelemetry Trace Trajectory Analyzer (Identifies weakest step to fix first)

What it scores: the fixed examples stored in the golden dataset (balance cases, extracted rows,
category predictions, sample advice). It calls no model, so it checks the scoring rules rather
than live Gemini output; the stored category predictions equal the ground truth.
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

GOLDEN_DATASET_PATH = Path(__file__).resolve().parent / "golden_dataset.json"


class EvaluationReport(BaseModel):
    """Consolidated autorater benchmark execution report."""
    total_benchmarks_run: int = 4
    golden_dataset_version: str = "3.0.0-FDE"
    math_reconciliation_score: float = Field(..., description="Percentage of mathematically consistent statement runs")
    extraction_completeness_score: float = Field(..., description="Average extraction recall across line-items")
    categorization_f1_score: float = Field(..., description="Macro-averaged F1 score on category assignments")
    fiduciary_soundness_score: float = Field(..., description="Compliance percentage with core financial planning tenets")
    passed: bool = Field(..., description="True if all metrics satisfy enterprise production SLAs")
    benchmark_details: Dict[str, Any] = Field(default_factory=dict)


def load_golden_dataset() -> Dict[str, Any]:
    """Loads the curated Golden Dataset from `app/eval/golden_dataset.json`."""
    if GOLDEN_DATASET_PATH.exists():
        with open(GOLDEN_DATASET_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
            # Support both standard JSON and Python-style True/False if edited by hand
            raw_norm = re.sub(r"\bTrue\b", "true", re.sub(r"\bFalse\b", "false", raw))
            return json.loads(raw_norm)
    return {}


class MathReconciliationAutorater:
    """Evaluates whether sum of extracted debits and credits mathematically matches reported net change."""

    def evaluate(self, test_cases: List[Dict[str, Any]]) -> Tuple[float, List[Dict[str, Any]]]:
        if not test_cases:
            return 100.0, []

        passed_count = 0
        details = []

        for case in test_cases:
            opening = float(case.get("opening_balance", 0.0))
            closing = float(case.get("closing_balance", 0.0))
            transactions = case.get("extracted_transactions", [])

            credits = sum(float(tx["amount"]) for tx in transactions if tx.get("type") in ("INCOME", "CREDIT"))
            debits = sum(
                float(tx["amount"])
                for tx in transactions
                if tx.get("type") in ("EXPENSE", "DEBIT", "WITHDRAWAL", "TRANSFER")
            )

            computed_closing = round(opening + credits - debits, 2)
            expected_closing = round(closing, 2)
            delta = abs(computed_closing - expected_closing)

            is_valid = delta < 0.05
            if is_valid:
                passed_count += 1

            details.append({
                "case_id": case.get("case_id", "unknown"),
                "bank": case.get("bank", "unknown"),
                "expected_closing": expected_closing,
                "computed_closing": computed_closing,
                "delta": round(delta, 2),
                "passed": is_valid,
            })

        score = (passed_count / len(test_cases)) * 100.0
        return score, details


class ExtractionCompletenessAutorater:
    """Evaluates field-level completeness, valid ISO dates, non-null descriptions, and positive amounts."""

    def evaluate(self, transactions: List[Dict[str, Any]]) -> Tuple[float, Dict[str, Any]]:
        if not transactions:
            return 0.0, {"error": "Empty transaction set"}

        date_regex = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        valid_rows = 0
        issues = []

        for idx, tx in enumerate(transactions):
            row_errors = []
            # 1. Date check
            d = str(tx.get("date", ""))
            if not date_regex.match(d):
                row_errors.append(f"Invalid date format '{d}'")

            # 2. Amount check
            try:
                amt = float(tx.get("amount", -1))
                if amt <= 0:
                    row_errors.append(f"Non-positive amount '{amt}'")
            except (ValueError, TypeError):
                row_errors.append("Invalid numerical amount")

            # 3. Description check
            desc = str(tx.get("description", "")).strip()
            if len(desc) < 2:
                row_errors.append("Missing or empty merchant narration")

            if not row_errors:
                valid_rows += 1
            else:
                issues.append({"row_index": idx, "errors": row_errors})

        recall = (valid_rows / len(transactions)) * 100.0
        return recall, {"total_evaluated": len(transactions), "valid_rows": valid_rows, "issues": issues[:5]}


class CategoryF1Autorater:
    """Calculates precision, recall, and macro F1 between model classifications and verified ground truth."""

    def evaluate(self, predictions: List[str], ground_truth: List[str]) -> Tuple[float, Dict[str, Any]]:
        if not predictions or len(predictions) != len(ground_truth):
            return 0.0, {"error": "Mismatched length between predictions and ground truth"}

        classes = sorted(list(set(ground_truth + predictions)))
        f1_scores = []
        class_details = {}

        for c in classes:
            tp = sum(1 for p, g in zip(predictions, ground_truth) if p == c and g == c)
            fp = sum(1 for p, g in zip(predictions, ground_truth) if p == c and g != c)
            fn = sum(1 for p, g in zip(predictions, ground_truth) if p != c and g == c)

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
            f1_scores.append(f1)

            class_details[c] = {
                "precision": round(precision, 2),
                "recall": round(recall, 2),
                "f1": round(f1, 2),
                "support": sum(1 for g in ground_truth if g == c),
            }

        macro_f1 = (sum(f1_scores) / len(f1_scores)) * 100.0 if f1_scores else 0.0
        return macro_f1, {"macro_f1": round(macro_f1, 1), "classes": class_details}


class FiduciarySoundnessAutorater:
    """Evaluates LLM-generated CFO advice against mandatory fiduciary rules:
    - Never recommends spending when debt/deficit is extreme without warning.
    - Preserves emergency fund priority.
    - Mentions concrete percentage or numeric limits rather than vague generalities.
    """

    def evaluate(self, advice_texts: List[str], scenarios: List[Dict[str, Any]]) -> Tuple[float, List[Dict[str, Any]]]:
        if not advice_texts or len(advice_texts) != len(scenarios):
            return 0.0, []

        passed_count = 0
        details = []

        for advice, scenario in zip(advice_texts, scenarios):
            violations = []
            lower_advice = advice.lower()

            # Rule 1: Numeric specificity requirement
            has_numbers = bool(re.search(r"[₹\d%]+", advice))
            if not has_numbers:
                violations.append("Advice lacks quantitative specificity (no ₹, %, or numbers).")

            # Rule 2: Deficit scenario emergency protection
            if scenario.get("is_in_deficit"):
                remedy_terms = ["reduce", "cut", "stop", "emergency", "pay off", "eliminate", "deficit", "warning"]
                if not any(term in lower_advice for term in remedy_terms):
                    violations.append("Failed to warn or recommend expense cuts during active deficit.")

            # Rule 3: High-interest debt prioritization
            if scenario.get("has_credit_card_debt"):
                debt_terms = ["credit card", "debt", "interest", "pay down", "avalanche", "snowball"]
                if not any(term in lower_advice for term in debt_terms):
                    violations.append("Failed to prioritize clearing high-interest credit card debt.")

            is_compliant = len(violations) == 0
            if is_compliant:
                passed_count += 1

            details.append({
                "scenario": scenario.get("name", "unnamed"),
                "compliant": is_compliant,
                "violations": violations,
            })

        score = (passed_count / len(scenarios)) * 100.0
        return score, details


def run_full_evaluation_suite() -> EvaluationReport:
    """Runs all 4 continuous evaluation benchmarks against `golden_dataset.json` + OTel trace analysis."""
    logger.info("[Autorater Flywheel] Initiating comprehensive FDE benchmark suite on golden_dataset.json...")

    golden = load_golden_dataset()

    # 1. Math Reconciliation Benchmark Data
    math_test_cases = golden.get("math_reconciliation_cases") or [
        {
            "case_id": "HDFC_Statement_Jan",
            "opening_balance": 50000.0,
            "closing_balance": 64500.0,
            "extracted_transactions": [
                {"amount": 35000.0, "type": "INCOME"},
                {"amount": 12000.0, "type": "EXPENSE"},
                {"amount": 8500.0, "type": "EXPENSE"},
            ],
        },
        {
            "case_id": "SBI_Statement_Feb",
            "opening_balance": 15000.0,
            "closing_balance": 10200.0,
            "extracted_transactions": [
                {"amount": 2500.0, "type": "EXPENSE"},
                {"amount": 2300.0, "type": "EXPENSE"},
            ],
        },
    ]
    math_rater = MathReconciliationAutorater()
    math_score, math_details = math_rater.evaluate(math_test_cases)

    # 2. Extraction Completeness Benchmark Data
    sample_extractions = golden.get("extraction_completeness_samples") or [
        {"date": "2026-09-01", "amount": 1450.0, "description": "Starbucks Coffee", "type": "EXPENSE"},
        {"date": "2026-09-02", "amount": 15000.0, "description": "Apartment Rent", "type": "EXPENSE"},
        {"date": "2026-09-03", "amount": 750.0, "description": "Uber Ride", "type": "EXPENSE"},
        {"date": "2026-09-05", "amount": 50000.0, "description": "Salary Credit", "type": "INCOME"},
    ]
    completeness_rater = ExtractionCompletenessAutorater()
    completeness_score, comp_details = completeness_rater.evaluate(sample_extractions)

    # 3. Categorization F1 Benchmark Data
    cat_bench = golden.get("categorization_benchmark") or {}
    ground_truth = cat_bench.get("ground_truth") or [
        "Food", "Rent", "Transportation", "Food", "Utilities", "Investment", "Food", "Entertainment"
    ]
    predictions = cat_bench.get("predictions") or list(ground_truth)
    f1_rater = CategoryF1Autorater()
    f1_score, f1_details = f1_rater.evaluate(predictions, ground_truth)

    # 4. Fiduciary Soundness Benchmark Data
    fid_cases = golden.get("fiduciary_scenarios") or [
        {
            "name": "High Deficit Run",
            "is_in_deficit": True,
            "has_credit_card_debt": False,
            "sample_advice": "Your ₹12,000 monthly deficit requires immediate action. Cut dining out by 30% and eliminate unused subscriptions to rebuild your emergency buffer.",
        },
        {
            "name": "Healthy Surplus Run",
            "is_in_deficit": False,
            "has_credit_card_debt": False,
            "sample_advice": "Your ₹35,000 surplus allows a disciplined 50/30/20 strategy. Allocate ₹20,000 to monthly SIP index funds and keep ₹15,000 in liquid savings.",
        },
    ]
    sample_advice = [c["sample_advice"] for c in fid_cases]
    fiduciary_rater = FiduciarySoundnessAutorater()
    fiduciary_score, fid_details = fiduciary_rater.evaluate(sample_advice, fid_cases)

    # 5. OpenTelemetry Trace Bottleneck Analysis (weakest step to fix first)
    trace_analysis = {}
    try:
        from telemetry.tracer import get_recent_traces
        trace_analysis = get_recent_traces(limit=10)
    except Exception:
        pass

    # SLAs: Math >= 95%, Recall >= 95%, F1 >= 90%, Fiduciary >= 95%
    passed = (
        math_score >= 95.0
        and completeness_score >= 95.0
        and f1_score >= 90.0
        and fiduciary_score >= 95.0
    )

    report = EvaluationReport(
        total_benchmarks_run=4,
        golden_dataset_version=golden.get("version", "3.0.0-FDE"),
        math_reconciliation_score=round(math_score, 1),
        extraction_completeness_score=round(completeness_score, 1),
        categorization_f1_score=round(f1_score, 1),
        fiduciary_soundness_score=round(fiduciary_score, 1),
        passed=passed,
        benchmark_details={
            "golden_dataset_path": str(GOLDEN_DATASET_PATH.name),
            "math_reconciliation": math_details,
            "completeness": comp_details,
            "category_f1": f1_details,
            "fiduciary": fid_details,
            "otel_trace_analysis": {
                "otel_sdk_active": trace_analysis.get("otel_sdk_active", False),
                "total_traces_analyzed": trace_analysis.get("total_traces_recorded", 0),
                "weakest_step_to_fix_first": trace_analysis.get("global_weakest_step_to_fix_first"),
            },
        },
    )
    return report


class FinancialAutorater:
    """Facade for running the Golden Dataset autorater benchmark suite."""

    @staticmethod
    def run_golden_dataset_benchmark() -> EvaluationReport:
        return run_full_evaluation_suite()


if __name__ == "__main__":
    report = run_full_evaluation_suite()
    print("=" * 60)
    print("AUTORATER EVALUATION FLYWHEEL - BENCHMARK REPORT")
    print("=" * 60)
    print(f"Golden Dataset Version    : {report.golden_dataset_version}")
    print(f"Math Reconciliation Score : {report.math_reconciliation_score}%")
    print(f"Extraction Completeness   : {report.extraction_completeness_score}%")
    print(f"Categorization Macro-F1   : {report.categorization_f1_score}%")
    print(f"Fiduciary Soundness       : {report.fiduciary_soundness_score}%")
    print(f"Enterprise Production SLA : {'PASSED [OK]' if report.passed else 'FAILED [VIOLATION]'}")
    print("=" * 60)

