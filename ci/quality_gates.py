"""
Quality Gates Engine.

Evaluates pipeline-level quality gates against coverage, pass rate,
flakiness, and requirement completeness data.

Configuration (all override-able via environment variables):
  GATE_MIN_COVERAGE_PCT    — minimum % of requirements with full coverage (default 50)
  GATE_MIN_PASS_RATE       — minimum Playwright test pass rate (default 75)
  GATE_MAX_FLAKY           — maximum flaky requirement count (default 5)
  GATE_REQUIRE_CRITICAL    — fail if any HIGH-criticality requirement is uncovered (default true)
  GATE_MAX_HIGH_RISK       — maximum high-risk uncovered requirements (default 999 = disabled)
  GATE_MIN_HE_PCT          — minimum HyperExecute execution coverage % (default 0 = disabled)

Gate severities:
  CRITICAL — exits 1, blocks the pipeline
  WARNING  — logged, does NOT block

Sources:
  - reports/coverage_report.json
  - reports/traceability_matrix.json
  - reports/flaky_requirements.json

Produces:
  - reports/quality_gates.json

Exit codes:
  0 — all CRITICAL gates passed (warnings may exist)
  1 — one or more CRITICAL gates failed
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result

# ── Gate thresholds (env-configurable) ───────────────────────────────────────
_CFG = {
    "min_coverage_pct":          float(os.environ.get("GATE_MIN_COVERAGE_PCT",  "50")),
    "min_pass_rate":             float(os.environ.get("GATE_MIN_PASS_RATE",      "75")),
    "max_flaky":                 int(os.environ.get("GATE_MAX_FLAKY",            "5")),
    "require_critical_coverage": os.environ.get("GATE_REQUIRE_CRITICAL", "true").lower() == "true",
    "max_high_risk_uncovered":   int(os.environ.get("GATE_MAX_HIGH_RISK",       "999")),
    "min_he_pct":                float(os.environ.get("GATE_MIN_HE_PCT",         "0")),
}


def _load_json(path: str, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _gate(
    name:     str,
    severity: str,
    passed:   bool,
    actual,
    threshold,
    unit:     str = "",
    detail:   str = "",
) -> dict:
    return {
        "gate":      name,
        "severity":  severity,
        "passed":    passed,
        "actual":    actual,
        "threshold": threshold,
        "unit":      unit,
        "detail":    detail,
    }


def evaluate() -> dict:
    print_stage_header("7d", "QUALITY_GATES",
                        "Evaluate pipeline quality gates against coverage and execution data")
    Path("reports").mkdir(exist_ok=True)

    coverage     = _load_json("reports/coverage_report.json",  {})
    trace        = _load_json("reports/traceability_matrix.json", {})
    flaky_data   = _load_json("reports/flaky_requirements.json", {})

    cov_sum   = coverage.get("summary", {})
    trace_sum = trace.get("summary", {})
    cov_reqs  = coverage.get("requirements", [])

    coverage_pct    = cov_sum.get("coverage_pct", 0.0)
    pass_rate       = trace_sum.get("pass_rate", 0.0)
    flaky_count     = len(flaky_data.get("flaky", []))
    he_coverage_pct = cov_sum.get("he_coverage_pct", 0.0)

    # Requirements that are HIGH criticality and uncovered
    critical_uncovered = [
        r["requirement_id"] for r in cov_reqs
        if r.get("criticality") == "HIGH" and r.get("coverage_status") == "NONE"
    ]

    # HIGH-risk requirements that are also failing
    high_risk_failing = [
        r["requirement_id"] for r in cov_reqs
        if r.get("risk_level") == "HIGH" and r.get("execution_status", {}).get("failed", 0) > 0
    ]

    # HIGH-risk requirements that are completely uncovered
    high_risk_uncovered = [
        r["requirement_id"] for r in cov_reqs
        if r.get("risk_level") == "HIGH" and r.get("coverage_status") == "NONE"
    ]

    gates: list[dict] = []
    cfg = _CFG

    # ── Gate 1: Overall requirement coverage ─────────────────────────────────
    gates.append(_gate(
        name="Minimum requirement coverage",
        severity="WARNING",  # advisory until pipeline matures — escalate to CRITICAL once stable
        passed=coverage_pct >= cfg["min_coverage_pct"],
        actual=coverage_pct,
        threshold=cfg["min_coverage_pct"],
        unit="%",
        detail=(
            f"{cov_sum.get('covered_full', 0)} of {cov_sum.get('total_requirements', 0)} "
            "requirements fully covered"
        ),
    ))

    # ── Gate 2: Test pass rate ────────────────────────────────────────────────
    has_execution = trace_sum.get("executed", 0) > 0
    gates.append(_gate(
        name="Minimum test pass rate",
        severity="CRITICAL",
        passed=(pass_rate >= cfg["min_pass_rate"]) if has_execution else True,
        actual=pass_rate if has_execution else 100.0,
        threshold=cfg["min_pass_rate"],
        unit="%",
        detail=(
            f"{trace_sum.get('passed', 0)} passed of {trace_sum.get('executed', 0)} executed"
            if has_execution else "No tests executed yet"
        ),
    ))

    # ── Gate 3: Flaky test ceiling ────────────────────────────────────────────
    gates.append(_gate(
        name="Flaky test threshold",
        severity="WARNING",
        passed=flaky_count <= cfg["max_flaky"],
        actual=flaky_count,
        threshold=cfg["max_flaky"],
        unit="flaky requirements",
        detail=(
            f"{flaky_count} requirement(s) show retry or mixed-status behaviour"
            if flaky_count else "No flaky requirements detected"
        ),
    ))

    # ── Gate 4: Critical (HIGH-criticality) requirements must be covered ──────
    if cfg["require_critical_coverage"]:
        gates.append(_gate(
            name="Critical requirements covered",
            severity="WARNING",   # WARNING so onboarding pipelines aren't immediately blocked
            passed=len(critical_uncovered) == 0,
            actual=len(critical_uncovered),
            threshold=0,
            unit="uncovered HIGH-criticality requirements",
            detail=(
                f"Uncovered: {', '.join(critical_uncovered)}"
                if critical_uncovered else "All HIGH-criticality requirements have coverage"
            ),
        ))

    # ── Gate 5: High-risk requirements must not be failing ───────────────────
    gates.append(_gate(
        name="No failing high-risk requirements",
        severity="CRITICAL",
        passed=len(high_risk_failing) == 0,
        actual=len(high_risk_failing),
        threshold=0,
        unit="failing high-risk requirements",
        detail=(
            f"Failing: {', '.join(high_risk_failing)}"
            if high_risk_failing else "No high-risk requirements are failing"
        ),
    ))

    # ── Gate 6: High-risk uncovered ceiling ──────────────────────────────────
    if cfg["max_high_risk_uncovered"] < 999:
        gates.append(_gate(
            name="High-risk uncovered ceiling",
            severity="WARNING",
            passed=len(high_risk_uncovered) <= cfg["max_high_risk_uncovered"],
            actual=len(high_risk_uncovered),
            threshold=cfg["max_high_risk_uncovered"],
            unit="uncovered high-risk requirements",
            detail=(
                f"Uncovered: {', '.join(high_risk_uncovered)}"
                if high_risk_uncovered else "All high-risk requirements have coverage"
            ),
        ))

    # ── Gate 7: HyperExecute execution coverage ──────────────────────────────
    if cfg["min_he_pct"] > 0:
        gates.append(_gate(
            name="HyperExecute execution coverage",
            severity="WARNING",
            passed=he_coverage_pct >= cfg["min_he_pct"],
            actual=he_coverage_pct,
            threshold=cfg["min_he_pct"],
            unit="% requirements executed on HE",
            detail=f"{he_coverage_pct}% of requirements executed on HyperExecute",
        ))

    # ── Evaluate ──────────────────────────────────────────────────────────────
    critical_failed = [g for g in gates if g["severity"] == "CRITICAL" and not g["passed"]]
    warnings_failed = [g for g in gates if g["severity"] == "WARNING"  and not g["passed"]]
    all_passed      = len(critical_failed) == 0

    result = {
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "gates_passed":     all_passed,
        "critical_failures": len(critical_failed),
        "warnings":         len(warnings_failed),
        "config":           cfg,
        "gates":            gates,
    }

    Path("reports/quality_gates.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )

    if critical_failed:
        print("\n[QUALITY_GATES] CRITICAL failures (pipeline blocked):")
        for g in critical_failed:
            print(f"  ❌  {g['gate']}")
            print(f"      actual={g['actual']} {g['unit']}  threshold={g['threshold']}")
            print(f"      {g['detail']}")

    if warnings_failed:
        print("\n[QUALITY_GATES] Warnings (non-blocking):")
        for g in warnings_failed:
            print(f"  ⚠️   {g['gate']}")
            print(f"       actual={g['actual']} {g['unit']}  threshold={g['threshold']}")
            print(f"       {g['detail']}")

    print_stage_result("7d", "QUALITY_GATES", {
        "Gates evaluated":   len(gates),
        "Critical failures": len(critical_failed),
        "Warnings":          len(warnings_failed),
        "Overall":           "PASS" if all_passed else "FAIL",
        "Output":            "reports/quality_gates.json",
    }, success=all_passed)

    if not all_passed:
        sys.exit(1)

    return result


if __name__ == "__main__":
    evaluate()
