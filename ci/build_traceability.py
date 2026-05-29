"""
Build traceability matrix from real execution artifacts only.

Data sources (all real, no fabrication):
  - requirements/analyzed_requirements.json  — Kane AI functional verification (Stage 1)
  - scenarios/scenarios.json                 — scenario catalog with function_name
  - reports/normalized_results.json          — normalized Playwright results (all browsers)
  - reports/api_details.json                 — HE session links (fallback for session URLs)
  - reports/junit-*.xml / reports/junit.xml  — raw pytest results (fallback when conftest absent)
  - reports/test_execution_manifest.json     — run type (full/incremental)

When data is missing: marks as "data_unavailable". Never fabricates values.
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result

# ── Feature + coverage-category metadata (imported from coverage_analysis) ───
_FEATURE_KEYWORDS: dict[str, list[str]] = {
    "SEARCH":         ["search", "find product", "search bar", "search result"],
    "CART":           ["cart", "add to cart", "shopping cart", "remove from cart",
                       "update quantity", "cart item", "line total"],
    "CATALOG":        ["catalog", "laptops", "product listing", "browse", "category", "grid"],
    "FILTER":         ["filter", "manufacturer", "brand filter", "narrow", "sidebar"],
    "PRODUCT_DETAIL": ["product detail", "detail page", "product name", "price", "thumbnail"],
    "GUEST":          ["guest", "without logging in", "guest browsing"],
    "AUTH":           ["register", "log in", "login", "log out", "logout",
                       "account", "first name", "telephone", "password", "dashboard"],
    "CHECKOUT":       ["checkout", "shipping", "flat rate", "shipping address"],
    "WISHLIST":       ["wish list", "wishlist"],
    "SORT":           ["sort", "price low to high", "listing order"],
}
_FEATURE_CRITICALITY: dict[str, str] = {
    "AUTH": "HIGH", "CHECKOUT": "HIGH", "CART": "HIGH",
    "SEARCH": "MEDIUM", "CATALOG": "MEDIUM", "PRODUCT_DETAIL": "MEDIUM",
    "FILTER": "LOW", "SORT": "LOW", "WISHLIST": "LOW", "GUEST": "LOW",
}
_NEGATIVE_KW = frozenset(["invalid", "error", "fail", "reject", "empty", "remove",
                           "delete", "cannot", "unauthorized", "no results"])
_EDGE_KW     = frozenset(["empty cart", "zero", "boundary", "duplicate", "persistence"])
_MOBILE_BR   = frozenset(["android", "ios", "safari_mobile", "mobile"])


def _classify_feature(text: str) -> str:
    text_lower = text.lower()
    best, best_n = "GENERAL", 0
    for feat, kws in _FEATURE_KEYWORDS.items():
        n = sum(1 for kw in kws if kw in text_lower)
        if n > best_n:
            best_n, best = n, feat
    return best

DEBUG = os.environ.get("REPORT_DEBUG", "false").lower() == "true"


def _debug(msg):
    if DEBUG:
        print(f"[REPORT_DEBUG] {msg}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", default="requirements/analyzed_requirements.json")
    parser.add_argument("--scenarios", default="scenarios/scenarios.json")
    parser.add_argument("--manifest", default="reports/test_execution_manifest.json")
    parser.add_argument("--normalized", default="reports/normalized_results.json")
    parser.add_argument("--api-details", default="reports/api_details.json")
    parser.add_argument("--out", default="reports/traceability_matrix.md")
    parser.add_argument("--json-out", default="reports/traceability_matrix.json")
    return parser.parse_args()


def load_json(path, default):
    p = Path(path)
    if not p.exists():
        _debug(f"File not found: {path}")
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        _debug(f"Failed to parse {path}: {exc}")
        return default


def _load_normalized_results(path):
    """
    Returns:
      results_by_scenario: {sc_id: [record, ...]}  — all browser results for each scenario
      all_browsers: sorted list of browsers seen across all results
    """
    raw = load_json(path, {})
    results = raw.get("results", [])
    by_sc: dict = {}
    browsers = set()
    for r in results:
        sc_id = r.get("scenario_id", "")
        by_sc.setdefault(sc_id, []).append(r)
        browsers.add(r.get("browser", "chrome"))
    _debug(f"Normalized results: {len(results)} records, {len(browsers)} browser(s): {sorted(browsers)}")
    return by_sc, sorted(browsers)


def _junit_fallback(junit_glob="reports"):
    """Last-resort: parse junit XML files directly. Returns {fn_name: {status, duration_ms}}."""
    results = {}
    reports_dir = Path(junit_glob)
    if not reports_dir.exists():
        return results
    for xml_file in sorted(reports_dir.glob("junit*.xml")):
        try:
            root = ET.fromstring(xml_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for testcase in root.iter("testcase"):
            name = testcase.attrib.get("name", "")
            duration_ms = round(float(testcase.attrib.get("time", "0") or "0") * 1000)
            if testcase.find("failure") is not None or testcase.find("error") is not None:
                status = "failed"
            elif testcase.find("skipped") is not None:
                status = "skipped"
            else:
                status = "passed"
            results[name] = {"status": status, "duration_ms": duration_ms}
    return results


def _overall_status(browser_records: list) -> str:
    """Aggregate across browsers: failed > skipped > passed (any) > data_unavailable.

    A test is PASSED if at least one browser passed and none failed.
    data_unavailable from secondary browsers does not block a pass verdict —
    it would incorrectly penalise tests where only one shard's artifact is missing.
    """
    if not browser_records:
        return "data_unavailable"
    statuses = [r.get("status", "data_unavailable") for r in browser_records]
    if "failed" in statuses:
        return "failed"
    if "passed" in statuses:
        return "passed"
    if "skipped" in statuses:
        return "skipped"
    return "data_unavailable"


def _browser_summary(browser_records: list, all_browsers: list) -> dict:
    """Returns {browser: status} for table display."""
    by_browser = {r["browser"]: r.get("status", "data_unavailable") for r in browser_records}
    return {b: by_browser.get(b, "data_unavailable") for b in all_browsers}


def compute_result_analysis(rows, summary, all_browsers):
    total = len(rows)
    kane_passed = sum(1 for r in rows if r.get("kane_ai_result") == "passed")
    kane_ran = sum(1 for r in rows if r.get("kane_ai_result") in ("passed", "failed"))
    kane_pass_rate = round((kane_passed / kane_ran) * 100, 1) if kane_ran else 0.0

    pw_pass_rate = summary.get("pass_rate", 0.0)

    failed_reqs = sorted({
        r["requirement_id"]
        for r in rows
        if r.get("kane_ai_result") == "failed" or r.get("playwright_status") == "failed"
    })

    key_findings = []
    for r in rows:
        req_id = r["requirement_id"]
        kane_fail = r.get("kane_ai_result") == "failed"
        pw_fail = r.get("playwright_status") == "failed"
        if kane_fail and pw_fail:
            key_findings.append(f"{req_id}: failed both Kane AI verification and Playwright regression.")
        elif kane_fail:
            key_findings.append(
                f"{req_id}: failed Kane AI verification; Playwright status is {r.get('playwright_status', 'unknown')}."
            )
        elif pw_fail:
            key_findings.append(f"{req_id}: passed Kane AI verification but failed Playwright regression.")

    unavailable = [r for r in rows if r.get("playwright_status") == "data_unavailable"]
    if unavailable:
        key_findings.append(
            f"{len(unavailable)} requirement(s) have no Playwright execution data (data_unavailable)."
        )

    if not key_findings:
        key_findings.append(
            "All tested requirements passed both Kane AI verification and Playwright regression."
        )

    has_failures = bool(failed_reqs)
    if pw_pass_rate >= 90 and not has_failures:
        risk_level = "low"
    elif pw_pass_rate >= 75:
        risk_level = "medium"
    else:
        risk_level = "high"

    overall_health = {"low": "healthy", "medium": "at_risk", "high": "critical"}[risk_level]

    untested = summary.get("untested_requirements", [])
    if overall_health == "healthy":
        recommendation_hint = (
            "All requirements passed verification and regression across all browsers; "
            "release can proceed with confidence."
        )
    elif untested:
        recommendation_hint = (
            f"Release blocked: {len(failed_reqs)} failing requirement(s) and "
            f"{len(untested)} with no execution data. Resolve before shipping."
        )
    else:
        recommendation_hint = (
            f"Release carries medium risk: {len(failed_reqs)} failing requirement(s). "
            "Review browser-specific failures before conditional approval."
        )

    return {
        "overall_health": overall_health,
        "kane_pass_rate": kane_pass_rate,
        "playwright_pass_rate": pw_pass_rate,
        "risk_level": risk_level,
        "key_findings": key_findings,
        "failed_requirements": failed_reqs,
        "recommendation_hint": recommendation_hint,
        "browsers_tested": all_browsers,
    }


def main():
    args = parse_args()
    print_stage_header("7", "TRACEABILITY_REPORT", "Build requirement → scenario → test → result matrix")
    Path("reports").mkdir(exist_ok=True)
    requirements = load_json(args.requirements, [])
    scenarios = load_json(args.scenarios, [])
    manifest = load_json(args.manifest, {})

    normalized_by_sc, all_browsers = _load_normalized_results(args.normalized)

    # Fallback: parse junit directly if normalized results are absent
    junit_fallback = {} if normalized_by_sc else _junit_fallback()
    _debug(f"JUnit fallback entries: {len(junit_fallback)}")

    # HE session links for scenarios that have no conftest result
    api_details = load_json(args.api_details, {})
    he_task_links = {
        (t.get("name") or "").strip(): t.get("session_link", "")
        for t in api_details.get("he_tasks", [])
        if t.get("name")
    }

    scenarios_by_req = {s["requirement_id"]: s for s in scenarios}

    rows = []
    executed = 0
    passed = 0
    untested = []
    failing = []

    for req in requirements:
        scenario = scenarios_by_req.get(req["id"])
        sc_id = scenario["id"] if scenario else "n/a"
        tc_id = scenario.get("test_case_id", "n/a") if scenario else "n/a"
        fn_name = scenario.get("function_name", f"test_{sc_id.lower().replace('-','_')}") if scenario else ""

        # ── Playwright results (multi-browser) ───────────────────────────────
        browser_records = normalized_by_sc.get(sc_id, [])

        if not browser_records and fn_name and fn_name in junit_fallback:
            # Fallback: build a synthetic-free record from junit only
            jdata = junit_fallback[fn_name]
            browser_records = [{
                "scenario_id": sc_id,
                "browser": "chrome",
                "status": jdata["status"],
                "duration_ms": jdata["duration_ms"],
                "session_link": he_task_links.get(fn_name, ""),
                "source": "junit",
            }]

        playwright_overall = _overall_status(browser_records)
        per_browser = _browser_summary(browser_records, all_browsers)

        # Best session link across browsers
        session_link = next(
            (r.get("session_link", "") for r in browser_records if r.get("session_link")),
            he_task_links.get(fn_name, ""),
        )

        # ── Kane AI result (Stage 1, always from analyzed_requirements.json) ─
        kane_status = req.get("kane_status") or "unknown"
        kane_links = req.get("kane_links", [])
        kane_session_link = kane_links[0] if kane_links else ""
        kane_one_liner = req.get("kane_one_liner", "")
        kane_summary = req.get("kane_summary", "")
        kane_steps = req.get("kane_steps", [])

        # ── Combined overall — BOTH Kane AND Playwright must pass ────────────
        if playwright_overall != "data_unavailable":
            executed += 1
            if playwright_overall == "passed" and kane_status == "passed":
                overall = "passed"
                passed += 1
            else:
                overall = "failed"
                failing.append(sc_id)
        else:
            untested.append(req["id"])
            overall = "data_unavailable"

        _debug(
            f"{req['id']}/{sc_id}: kane={kane_status} playwright={playwright_overall} "
            f"overall={overall} browsers={per_browser}"
        )

        # Feature + coverage-category annotation
        description = req.get("description", "")
        feature      = _classify_feature(description)
        criticality  = _FEATURE_CRITICALITY.get(feature, "MEDIUM")
        combined_txt = description.lower()
        browsers_run = {r.get("browser", "") for r in browser_records
                        if r.get("status") not in ("data_unavailable", None)}
        coverage_categories = {
            "happy_path":  sc_id != "n/a",
            "negative":    any(kw in combined_txt for kw in _NEGATIVE_KW),
            "edge_case":   any(kw in combined_txt for kw in _EDGE_KW),
            "mobile":      bool(browsers_run & _MOBILE_BR),
            "android":     "android" in browsers_run,
            "he_executed": bool(session_link),
            "regression":  playwright_overall not in ("data_unavailable", None),
        }

        rows.append({
            "requirement_id": req["id"],
            "acceptance_criterion": description,
            "scenario_id": sc_id,
            "test_case_id": tc_id,
            "function_name": fn_name,
            "feature": feature,
            "criticality": criticality,
            "coverage_categories": coverage_categories,
            "kane_ai_result": kane_status,
            "kane_session_link": kane_session_link,
            "kane_one_liner": kane_one_liner,
            "kane_summary": kane_summary,
            "kane_steps": kane_steps,
            "playwright_status": playwright_overall,
            "playwright_per_browser": per_browser,
            "session_link": session_link,
            "overall": overall,
        })

    pass_rate = round((passed / executed) * 100, 1) if executed else 0.0
    untested_to_report = untested if executed > 0 else []

    summary = {
        "run_type": manifest.get("run_type", "unknown"),
        "requirements_covered": len([r for r in rows if r["scenario_id"] != "n/a"]),
        "requirements_total": len(requirements),
        "executed": executed,
        "passed": passed,
        "pass_rate": pass_rate,
        "browsers_tested": all_browsers,
        "untested_requirements": untested_to_report,
        "failing_scenarios": [s for s in failing if s != "n/a"],
    }

    result_analysis = compute_result_analysis(rows, summary, all_browsers)

    # ── Markdown ──────────────────────────────────────────────────────────────
    browser_cols = " | ".join(b.capitalize() for b in all_browsers) if all_browsers else "Browser"
    browser_sep = " | ".join("---" for _ in (all_browsers or ["chrome"]))

    lines = [
        "# Traceability Matrix",
        "",
        f"- Run type: {summary['run_type']}",
        f"- Requirements covered: {summary['requirements_covered']}/{summary['requirements_total']}",
        f"- Browsers tested: {', '.join(all_browsers) or 'none'}",
        f"- Playwright pass rate: {summary['pass_rate']}% "
        f"({summary['passed']} passed, {summary['executed'] - summary['passed']} failed or skipped)",
        "",
        f"| Req ID | Acceptance Criterion | Scenario | Test Case | Kane Verify | Kane Session | What Kane Saw | {browser_cols} | Playwright | Session | Overall |",
        f"|---|---|---|---|---|---|---|{browser_sep}|---|---|---|",
    ]

    for row in rows:
        one_liner = row.get("kane_one_liner") or "—"
        kane_link = row.get("kane_session_link", "")
        kane_cell = f"[session]({kane_link})" if kane_link else "—"
        sel_link = row.get("session_link", "")
        sel_cell = f"[session]({sel_link})" if sel_link else "—"
        per_browser = row.get("playwright_per_browser", {})
        browser_cells = " | ".join(per_browser.get(b, "—") for b in all_browsers) if all_browsers else "—"
        criterion = row["acceptance_criterion"]
        lines.append(
            f"| {row['requirement_id']} "
            f"| {criterion} "
            f"| {row['scenario_id']} "
            f"| {row['test_case_id']} "
            f"| {row['kane_ai_result']} "
            f"| {kane_cell} "
            f"| {one_liner} "
            f"| {browser_cells} "
            f"| {row['playwright_status']} "
            f"| {sel_cell} "
            f"| {row['overall']} |"
        )

    # Kane AI detail section
    lines.extend(["", "## Kane AI Verification Detail", ""])
    for row in rows:
        lines.append(f"### {row['requirement_id']} — {row['acceptance_criterion']}")
        if row.get("kane_one_liner"):
            lines.append(f"> {row['kane_one_liner']}")
        lines.append("")
        if row.get("kane_steps"):
            lines.append("**Steps observed by Kane AI:**")
            lines.extend([f"- {step}" for step in row["kane_steps"]])
            lines.append("")
        if row.get("kane_summary"):
            lines.append(f"**Full summary:** {row['kane_summary']}")
            lines.append("")
        if row.get("kane_session_link"):
            lines.append(
                f"**Session:** [{row['kane_session_link']}]({row['kane_session_link']})"
            )
            lines.append("")

    kane_only_issues = [
        r for r in rows
        if r["playwright_status"] == "passed" and r["kane_ai_result"] == "failed"
    ]
    if kane_only_issues:
        lines.extend(["", "## Kane Analysis Warnings", ""])
        for r in kane_only_issues:
            lines.append(
                f"- {r['scenario_id']}: Kane returned `failed` while Playwright passed."
            )

    if summary["untested_requirements"]:
        lines.extend(["", "## No Execution Data", ""])
        for item in summary["untested_requirements"]:
            lines.append(f"- {item}: no Playwright execution data (data_unavailable)")

    if summary["failing_scenarios"]:
        lines.extend(["", "## Failing Scenarios", ""])
        for item in summary["failing_scenarios"]:
            lines.append(f"- {item}")

    lines.extend(["", "## Result Analysis", ""])
    lines.extend([
        f"- **Overall health:** {result_analysis['overall_health']}",
        f"- **Risk level:** {result_analysis['risk_level']}",
        f"- **Kane AI pass rate:** {result_analysis['kane_pass_rate']}%",
        f"- **Playwright pass rate:** {result_analysis['playwright_pass_rate']}%",
        f"- **Browsers tested:** {', '.join(result_analysis['browsers_tested']) or 'none'}",
        "",
    ])
    if result_analysis["failed_requirements"]:
        lines.append("**Failed requirements:**")
        lines.extend([f"- {r}" for r in result_analysis["failed_requirements"]])
        lines.append("")
    lines.append("**Key findings:**")
    lines.extend([f"- {f}" for f in result_analysis["key_findings"]])
    lines.extend(["", f"**Recommendation:** {result_analysis['recommendation_hint']}", ""])

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    json_path = Path(args.json_out)
    json_path.write_text(
        json.dumps({"summary": summary, "rows": rows, "result_analysis": result_analysis}, indent=2) + "\n",
        encoding="utf-8",
    )

    failing_count = summary.get("executed", 0) - summary.get("passed", 0)
    print_stage_result("7", "TRACEABILITY_REPORT", {
        "Requirements covered": f"{summary['requirements_covered']}/{summary['requirements_total']}",
        "Pass rate":            f"{pass_rate}%",
        "Passed":               summary.get("passed", 0),
        "Failed":               failing_count,
        "Untested":             len(summary.get("untested_requirements", [])),
        "Browsers":             ", ".join(all_browsers) or "none",
        "Output":               f"{args.out}, {args.json_out}",
    })


if __name__ == "__main__":
    main()
