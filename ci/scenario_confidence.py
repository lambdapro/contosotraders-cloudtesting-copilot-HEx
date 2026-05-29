"""
Scenario Confidence Analysis Engine.

Scores each scenario's test coverage sufficiency across multiple dimensions:
  - happy_path: Does the scenario cover the primary success flow?
  - negative: Are failure/rejection cases tested?
  - edge_case: Are boundary/unusual inputs covered?
  - mobile: Is mobile/responsive behavior considered?

Produces reports consumed by ConfidenceAnalysisSkill and ChatReporter.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Keywords that indicate negative/edge-case coverage in scenario descriptions
_NEGATIVE_KEYWORDS = re.compile(
    r"invalid|error|fail|reject|wrong|bad|empty|null|missing|exceed|over limit|"
    r"negative|edge|boundary|out of stock|no results|unauthorized|forbidden|expire",
    re.IGNORECASE,
)
_EDGE_KEYWORDS = re.compile(
    r"special character|unicode|long string|max|min|limit|boundary|zero|"
    r"empty cart|no item|already exists|duplicate|concurrent",
    re.IGNORECASE,
)
_MOBILE_KEYWORDS = re.compile(
    r"mobile|responsive|tablet|touch|swipe|viewport|ios|android|small screen",
    re.IGNORECASE,
)

# Features that are always considered HIGH criticality
_HIGH_CRITICALITY_FEATURES = {"CART", "CHECKOUT", "AUTH", "PAYMENT"}
_MEDIUM_CRITICALITY_FEATURES = {"SEARCH", "CATALOG", "PRODUCT_DETAIL", "WISHLIST"}


def _score_scenario(
    requirement: dict,
    scenario: dict,
    playwright_bodies: dict[str, str],
) -> dict:
    """Compute confidence score for one requirement/scenario pair."""
    req_text  = requirement.get("description", "")
    sc_text   = scenario.get("description", "") + " " + req_text
    feature   = scenario.get("feature", "GENERAL")
    sc_id     = scenario.get("id", "")
    kane_status = requirement.get("kane_status", "not_run")

    # Dimension scoring
    has_happy     = True   # Any scenario implicitly covers happy path
    has_negative  = bool(_NEGATIVE_KEYWORDS.search(sc_text))
    has_edge      = bool(_EDGE_KEYWORDS.search(sc_text))
    has_mobile    = bool(_MOBILE_KEYWORDS.search(sc_text))

    # Check if test body is more than the generic fallback
    body = playwright_bodies.get(sc_id, "")
    has_real_body = bool(body and "page.title" not in body)

    criticality = (
        "HIGH" if feature in _HIGH_CRITICALITY_FEATURES
        else "MEDIUM" if feature in _MEDIUM_CRITICALITY_FEATURES
        else "LOW"
    )

    # Coverage gaps
    gaps: list[str] = []
    if not has_negative:
        gaps.append("Missing negative/error scenario coverage")
    if not has_edge and criticality == "HIGH":
        gaps.append("Missing edge-case coverage for high-criticality feature")
    if not has_mobile and feature in _HIGH_CRITICALITY_FEATURES:
        gaps.append("No mobile coverage specified")
    if kane_status == "failed":
        gaps.append("Kane AI functional verification failed")
    if kane_status == "not_run":
        gaps.append("Kane AI verification not yet executed")

    # Confidence level
    gap_count = len(gaps)
    if gap_count == 0:
        confidence = "HIGH"
    elif gap_count <= 1 and criticality != "HIGH":
        confidence = "HIGH"
    elif gap_count <= 2:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Override: Kane failure → always LOW
    if kane_status == "failed":
        confidence = "LOW"

    # Build recommendation list
    recommendations: list[str] = []
    if not has_negative:
        recommendations.append(f"Add scenario: '{req_text[:60]}' with invalid/error conditions")
    if not has_edge and criticality == "HIGH":
        recommendations.append("Add boundary condition test cases")
    if not has_mobile:
        recommendations.append("Consider adding mobile viewport test")

    confidence_reason = "; ".join(gaps) if gaps else "All coverage dimensions satisfied"

    return {
        "requirement_id": requirement.get("id", ""),
        "scenario_id": sc_id,
        "scenario_status": scenario.get("status", "active"),
        "acceptance_criterion": req_text,
        "feature": feature,
        "criticality": criticality,
        "kane_status": kane_status,
        "confidence_level": confidence,
        "coverage_dimensions": {
            "happy_path":  has_happy,
            "negative":    has_negative,
            "edge_case":   has_edge,
            "mobile":      has_mobile,
            "real_body":   has_real_body,
        },
        "coverage_gaps": gaps,
        "recommendations": recommendations,
        "confidence_reason": confidence_reason,
        "risk_assessment": {
            "criticality": criticality,
            "risk_level": "HIGH" if confidence == "LOW" and criticality == "HIGH" else
                          "MEDIUM" if confidence != "HIGH" else "LOW",
        },
    }


def run_confidence_analysis(
    requirements: list[dict],
    scenarios: list[dict],
    playwright_bodies: dict[str, str] | None = None,
    output_dir: str = "reports",
) -> dict:
    """
    Score all active scenarios and produce confidence report files.

    Returns a report dict with "summary" and "records" (per-scenario scores).
    Writes:
      - {output_dir}/scenario-confidence-report.json
      - {output_dir}/requirement-confidence-summary.md
      - {output_dir}/high-risk-requirements.json
      - {output_dir}/coverage-gap-analysis.json
    """
    bodies = playwright_bodies or {}
    out    = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Build quick lookup maps
    req_by_id: dict[str, dict] = {r["id"]: r for r in requirements if r.get("id")}
    sc_by_req: dict[str, dict] = {}
    for sc in scenarios:
        if sc.get("requirement_id") and sc.get("status") != "deprecated":
            sc_by_req[sc["requirement_id"]] = sc

    records: list[dict] = []
    for req in requirements:
        rid = req.get("id", "")
        sc  = sc_by_req.get(rid)
        if not sc:
            # Requirement has no scenario → synthesise a placeholder
            sc = {"id": "", "description": req.get("description", ""), "feature": "GENERAL",
                  "status": "missing"}
        records.append(_score_scenario(req, sc, bodies))

    # Aggregate summary
    by_level: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for r in records:
        by_level[r["confidence_level"]] = by_level.get(r["confidence_level"], 0) + 1

    high_risk = [r for r in records if r["confidence_level"] == "LOW"]
    missing_neg = [r for r in records if "Missing negative" in " ".join(r.get("coverage_gaps", []))]

    quality_signals = {
        "confidence_gate_passed": by_level["LOW"] == 0,
        "high_criticality_low_confidence": [
            r["scenario_id"] for r in high_risk
            if r.get("risk_assessment", {}).get("criticality") == "HIGH"
        ],
        "missing_negative_coverage_count": len(missing_neg),
    }

    summary = {
        "total_requirements": len(requirements),
        "by_confidence_level": by_level,
        "high_confidence_count": by_level["HIGH"],
        "critical_gap_count": by_level["LOW"],
        "missing_negative_coverage": len(missing_neg),
        "missing_edge_case_coverage": sum(
            1 for r in records
            if not r["coverage_dimensions"].get("edge_case")
        ),
        "kane_failed_requirements": sum(1 for r in records if r["kane_status"] == "failed"),
        "no_mobile_coverage": sum(
            1 for r in records if not r["coverage_dimensions"].get("mobile")
        ),
        "quality_signals": quality_signals,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

    report = {"summary": summary, "records": records}

    # Write reports
    (out / "scenario-confidence-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    # High-risk requirements
    (out / "high-risk-requirements.json").write_text(
        json.dumps({
            "high_risk_count": len(high_risk),
            "requirements": [r["requirement_id"] for r in high_risk],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2) + "\n", encoding="utf-8"
    )

    # Coverage gap analysis
    (out / "coverage-gap-analysis.json").write_text(
        json.dumps({
            "missing_negative_coverage": [r["requirement_id"] for r in missing_neg],
            "missing_edge_case_coverage": [
                r["requirement_id"] for r in records
                if not r["coverage_dimensions"].get("edge_case")
            ],
            "kane_failures": [r["requirement_id"] for r in records if r["kane_status"] == "failed"],
            "missing_mobile_coverage": [
                r["requirement_id"] for r in records
                if not r["coverage_dimensions"].get("mobile")
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2) + "\n", encoding="utf-8"
    )

    # Markdown summary
    md_lines = [
        "# Scenario Confidence Report",
        "",
        f"**Total requirements:** {summary['total_requirements']}  ",
        f"**HIGH confidence:** {by_level['HIGH']}  ",
        f"**MEDIUM confidence:** {by_level['MEDIUM']}  ",
        f"**LOW confidence:** {by_level['LOW']}",
        "",
        "## Low Confidence Scenarios",
        "",
    ]
    for r in high_risk:
        md_lines.append(f"- **{r['scenario_id']}** ({r['requirement_id']}): {r['confidence_reason']}")
    if not high_risk:
        md_lines.append("_None — all scenarios have acceptable confidence_")
    (out / "requirement-confidence-summary.md").write_text(
        "\n".join(md_lines) + "\n", encoding="utf-8"
    )

    return report
