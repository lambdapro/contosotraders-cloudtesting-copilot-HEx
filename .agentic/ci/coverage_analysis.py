"""
Enterprise Requirement Coverage Analysis Engine.

Reads real pipeline artifacts to compute:
  - Per-requirement coverage status: FULL / PARTIAL / NONE
  - Coverage categories: happy_path, negative, edge_case, mobile, android, he_executed, regression
  - Flakiness: retries > 0 or mixed status across browsers
  - Missing scenario types vs. a feature-level risk model
  - Risk level per requirement: HIGH / MEDIUM / LOW
  - Feature-level rollup and coverage heatmap

Sources:
  - requirements/analyzed_requirements.json  (Kane AI results)
  - scenarios/scenarios.json                 (scenario catalog)
  - reports/normalized_results.json          (execution results per scenario/browser)

Produces:
  - reports/coverage_report.json
  - reports/missing_scenarios.json
  - reports/flaky_requirements.json
  - reports/coverage_report.md
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result

# ── Feature taxonomy ─────────────────────────────────────────────────────────
# Maps feature name → keywords to detect in requirement text
FEATURE_KEYWORDS: dict[str, list[str]] = {
    "SEARCH":         ["search", "find product", "search bar", "search result", "search for"],
    "CART":           ["cart", "add to cart", "shopping cart", "remove from cart",
                       "update quantity", "cart item", "line total", "cart update"],
    "CATALOG":        ["catalog", "laptops", "product listing", "product catalog",
                       "browse", "category", "grid", "product grid"],
    "FILTER":         ["filter", "manufacturer", "brand filter", "narrow", "sidebar"],
    "PRODUCT_DETAIL": ["product detail", "detail page", "product name", "price",
                       "thumbnail", "product page", "open a product"],
    "GUEST":          ["guest", "without logging in", "guest browsing"],
    "AUTH":           ["register", "log in", "login", "log out", "logout",
                       "account", "first name", "last name", "telephone",
                       "password", "dashboard", "registered"],
    "CHECKOUT":       ["checkout", "shipping", "flat rate", "shipping address",
                       "complete a guest checkout"],
    "WISHLIST":       ["wish list", "wishlist"],
    "SORT":           ["sort", "price low to high", "listing order"],
}

# ── Coverage category detection keywords ─────────────────────────────────────
_NEGATIVE_KEYWORDS = frozenset([
    "invalid", "error", "fail", "reject", "empty", "negative", "incorrect",
    "wrong", "missing", "cannot", "unable", "remove", "delete", "out of stock",
    "unauthorized", "no results", "not found", "forbidden", "limit",
])
_EDGE_CASE_KEYWORDS = frozenset([
    "empty cart", "zero quantity", "minimum", "maximum", "boundary",
    "already in cart", "duplicate", "special character", "persistence",
    "session expired",
])
_SECURITY_KEYWORDS = frozenset([
    "xss", "sql inject", "brute force", "unauthorized access", "csrf",
    "privilege escalation", "bypass", "tamper",
])
_MOBILE_BROWSERS = frozenset(["android", "ios", "safari_mobile", "mobile"])
_ANDROID_BROWSERS = frozenset(["android"])

# ── Business criticality ──────────────────────────────────────────────────────
FEATURE_CRITICALITY: dict[str, str] = {
    "AUTH":           "HIGH",
    "CHECKOUT":       "HIGH",
    "CART":           "HIGH",
    "SEARCH":         "MEDIUM",
    "CATALOG":        "MEDIUM",
    "PRODUCT_DETAIL": "MEDIUM",
    "FILTER":         "LOW",
    "SORT":           "LOW",
    "WISHLIST":       "LOW",
    "GUEST":          "LOW",
}

# ── Expected scenario types per feature (static risk model) ──────────────────
# Drives gap detection: which scenario types SHOULD exist but don't
EXPECTED_SCENARIOS: dict[str, list[dict]] = {
    "CART": [
        {"type": "happy_path", "description": "Add product to cart and see count update"},
        {"type": "happy_path", "description": "View cart with added items"},
        {"type": "happy_path", "description": "Remove item from cart"},
        {"type": "happy_path", "description": "Update item quantity and verify line total"},
        {"type": "negative",   "description": "Attempt to add out-of-stock product"},
        {"type": "edge_case",  "description": "View empty cart state"},
        {"type": "edge_case",  "description": "Cart persistence after page reload"},
    ],
    "SEARCH": [
        {"type": "happy_path", "description": "Search by product name and see results"},
        {"type": "negative",   "description": "Search with a term that returns no results"},
        {"type": "edge_case",  "description": "Search with special characters"},
        {"type": "edge_case",  "description": "Search with empty string"},
    ],
    "CATALOG": [
        {"type": "happy_path", "description": "Browse product category/catalog page"},
        {"type": "happy_path", "description": "View product grid layout"},
        {"type": "negative",   "description": "Visit category with no products"},
    ],
    "FILTER": [
        {"type": "happy_path", "description": "Apply manufacturer filter from sidebar"},
        {"type": "negative",   "description": "Apply filter that produces no results"},
    ],
    "PRODUCT_DETAIL": [
        {"type": "happy_path", "description": "View product detail page with name and price"},
        {"type": "happy_path", "description": "View product images gallery"},
    ],
    "GUEST": [
        {"type": "happy_path", "description": "Browse site as guest without login"},
    ],
    "AUTH": [
        {"type": "happy_path", "description": "Register new account with all fields"},
        {"type": "happy_path", "description": "Login with valid credentials and reach dashboard"},
        {"type": "happy_path", "description": "Logout and redirect to home page"},
        {"type": "negative",   "description": "Login with invalid credentials"},
        {"type": "negative",   "description": "Register with already-used email"},
        {"type": "edge_case",  "description": "Password strength validation"},
    ],
    "CHECKOUT": [
        {"type": "happy_path", "description": "Complete guest checkout with shipping address"},
        {"type": "negative",   "description": "Checkout with invalid shipping address"},
        {"type": "edge_case",  "description": "Checkout with empty cart"},
    ],
    "WISHLIST": [
        {"type": "happy_path", "description": "Add product to wishlist from detail page"},
        {"type": "happy_path", "description": "View wishlist items"},
        {"type": "negative",   "description": "Add duplicate product to wishlist"},
    ],
    "SORT": [
        {"type": "happy_path", "description": "Sort products by price low to high"},
        {"type": "happy_path", "description": "Verify listing order changes after sort"},
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: str, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _classify_feature(text: str) -> str:
    """Pick the best-matching feature for a requirement based on keyword density."""
    text_lower = text.lower()
    best_feature, best_count = "GENERAL", 0
    for feature, keywords in FEATURE_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > best_count:
            best_count, best_feature = count, feature
    return best_feature


def _compute_risk_level(
    feature: str,
    coverage_status: str,
    has_failures: bool,
    has_flaky: bool,
    has_negative: bool,
) -> str:
    criticality = FEATURE_CRITICALITY.get(feature, "MEDIUM")
    if has_failures and criticality == "HIGH":
        return "HIGH"
    if coverage_status == "NONE" and criticality != "LOW":
        return "HIGH"
    if coverage_status == "NONE":
        return "MEDIUM"
    if has_failures:
        return "HIGH" if criticality == "HIGH" else "MEDIUM"
    if criticality == "HIGH" and not has_negative:
        return "MEDIUM"
    if has_flaky:
        return "MEDIUM"
    if coverage_status == "PARTIAL":
        return "MEDIUM"
    return "LOW"


def _is_flaky(results: list[dict]) -> tuple[bool, int]:
    """Flaky if retries > 0 OR mixed statuses across browsers for same scenario."""
    total_retries = sum(r.get("retries", 0) for r in results)
    live = [r.get("status") for r in results
            if r.get("status") not in ("data_unavailable", None)]
    mixed = len(set(live)) > 1 if len(live) >= 2 else False
    return (total_retries > 0 or mixed), total_retries


def _missing_scenarios(
    feature: str,
    covered_descriptions: list[str],
    has_negative: bool,
    has_edge_case: bool,
) -> list[dict]:
    """Compare feature's expected scenario model against what's actually covered."""
    expected = EXPECTED_SCENARIOS.get(feature, [])
    missing = []
    covered_text = " ".join(d.lower() for d in covered_descriptions)
    for exp in expected:
        stype = exp["type"]
        if stype == "negative" and not has_negative:
            missing.append(exp)
        elif stype == "edge_case" and not has_edge_case:
            missing.append(exp)
        elif stype == "happy_path":
            # Heuristic: check if key words from expected description appear in covered texts
            words = [w for w in exp["description"].lower().split() if len(w) > 4]
            if words and not any(w in covered_text for w in words):
                missing.append(exp)
    return missing


# ── Main analysis ─────────────────────────────────────────────────────────────

def analyze(
    requirements_path: str = "requirements/analyzed_requirements.json",
    scenarios_path:    str = "scenarios/scenarios.json",
    normalized_path:   str = "reports/normalized_results.json",
) -> dict:
    print_stage_header("7b", "COVERAGE_ANALYSIS",
                        "Enterprise requirement coverage and gap analysis")
    Path("reports").mkdir(exist_ok=True)

    requirements     = _load_json(requirements_path, [])
    scenarios        = _load_json(scenarios_path, [])
    normalized_raw   = _load_json(normalized_path, {})
    normalized       = normalized_raw.get("results", [])

    # Build lookup tables
    scenarios_by_req: dict[str, list[dict]] = {}
    for sc in scenarios:
        if sc.get("status") != "deprecated":
            scenarios_by_req.setdefault(sc.get("requirement_id", ""), []).append(sc)

    results_by_sc: dict[str, list[dict]] = {}
    for r in normalized:
        results_by_sc.setdefault(r.get("scenario_id", ""), []).append(r)

    coverage_records   = []
    missing_all        = []
    flaky_requirements = []

    # Aggregate counters
    cnt_full = cnt_partial = cnt_none = 0
    cnt_negative = cnt_he = cnt_mobile = cnt_android = 0

    for req in requirements:
        req_id      = req.get("id", "")
        description = req.get("description", "")
        kane_status = req.get("kane_status", "unknown")
        feature     = _classify_feature(description)

        mapped_scenarios = scenarios_by_req.get(req_id, [])
        sc_ids = [s["id"] for s in mapped_scenarios]

        # All execution results for this requirement's scenarios
        all_results: list[dict] = []
        for sc_id in sc_ids:
            all_results.extend(results_by_sc.get(sc_id, []))

        # Execution stats (exclude data_unavailable from counts)
        live_results  = [r for r in all_results if r.get("status") != "data_unavailable"]
        exec_passed   = sum(1 for r in live_results if r.get("status") == "passed")
        exec_failed   = sum(1 for r in live_results if r.get("status") == "failed")
        exec_skipped  = sum(1 for r in live_results if r.get("status") == "skipped")
        exec_unavail  = sum(1 for r in all_results  if r.get("status") == "data_unavailable")
        exec_total    = len(live_results)

        # Browser coverage
        browsers_run = {r.get("browser", "") for r in live_results}
        has_mobile   = bool(browsers_run & _MOBILE_BROWSERS)
        has_android  = bool(browsers_run & _ANDROID_BROWSERS)
        he_executed  = any(r.get("session_link") for r in all_results)

        # Flakiness
        is_flaky, retry_count = _is_flaky(all_results)

        # Coverage category detection from all text
        all_texts = [description] + [s.get("source_description", "") for s in mapped_scenarios]
        combined_lower = " ".join(all_texts).lower()
        has_negative = any(kw in combined_lower for kw in _NEGATIVE_KEYWORDS)
        has_edge     = any(kw in combined_lower for kw in _EDGE_CASE_KEYWORDS)
        has_security = any(kw in combined_lower for kw in _SECURITY_KEYWORDS)

        coverage_categories = {
            "happy_path":      bool(mapped_scenarios),
            "negative":        has_negative,
            "edge_case":       has_edge,
            "security":        has_security,
            "mobile":          has_mobile,
            "android":         has_android,
            "he_executed":     he_executed,
            "regression":      exec_total > 0,
            "api_integration": False,
            "accessibility":   False,
        }

        # Coverage status
        if not mapped_scenarios:
            coverage_status = "NONE"
            cnt_none += 1
        elif exec_total == 0:
            coverage_status = "PARTIAL"   # scenario exists but did not run
            cnt_partial += 1
        elif exec_failed > 0:
            coverage_status = "PARTIAL"   # ran but not all passed
            cnt_partial += 1
        else:
            coverage_status = "FULL"
            cnt_full += 1

        if has_negative:  cnt_negative += 1
        if he_executed:   cnt_he       += 1
        if has_mobile:    cnt_mobile   += 1
        if has_android:   cnt_android  += 1

        # Risk level
        has_failures = exec_failed > 0 or kane_status == "failed"
        risk_level = _compute_risk_level(
            feature=feature,
            coverage_status=coverage_status,
            has_failures=has_failures,
            has_flaky=is_flaky,
            has_negative=has_negative,
        )

        # Expected scenario types vs actual
        missing = _missing_scenarios(
            feature=feature,
            covered_descriptions=all_texts,
            has_negative=has_negative,
            has_edge_case=has_edge,
        )
        if missing:
            missing_all.append({
                "requirement_id": req_id,
                "feature":        feature,
                "criticality":    FEATURE_CRITICALITY.get(feature, "MEDIUM"),
                "missing":        missing,
            })

        # Functional coverage %: active scenarios vs expected happy-path count
        n_happy_exp = max(1, len([e for e in EXPECTED_SCENARIOS.get(feature, [])
                                  if e["type"] == "happy_path"]))
        n_happy_cov = len(sc_ids)
        functional_pct = min(100.0, round(n_happy_cov / n_happy_exp * 100, 1))

        # Negative coverage %
        n_neg_exp  = len([e for e in EXPECTED_SCENARIOS.get(feature, []) if e["type"] == "negative"])
        negative_pct = 100.0 if (n_neg_exp == 0) else (100.0 if has_negative else 0.0)

        record = {
            "requirement_id":       req_id,
            "description":          description,
            "feature":              feature,
            "criticality":          FEATURE_CRITICALITY.get(feature, "MEDIUM"),
            "kane_status":          kane_status,
            "coverage_status":      coverage_status,
            "covered_scenarios":    sc_ids,
            "missing_scenarios":    missing,
            "execution_status": {
                "total":            exec_total,
                "passed":           exec_passed,
                "failed":           exec_failed,
                "skipped":          exec_skipped,
                "data_unavailable": exec_unavail,
                "flaky":            1 if is_flaky else 0,
            },
            "coverage_categories":  coverage_categories,
            "browsers_tested":      sorted(browsers_run),
            "functional_coverage_pct": functional_pct,
            "negative_coverage_pct":   negative_pct,
            "risk_level":           risk_level,
            "flaky":                is_flaky,
            "retry_count":          retry_count,
        }
        coverage_records.append(record)

        if is_flaky:
            flaky_requirements.append({
                "requirement_id": req_id,
                "feature":        feature,
                "retry_count":    retry_count,
                "scenarios":      sc_ids,
                "browsers_with_retries": sorted({
                    r.get("browser", "") for r in all_results if r.get("retries", 0) > 0
                }),
            })

    total = len(requirements)

    def _pct(n: int) -> float:
        return round(n / total * 100, 1) if total else 0.0

    summary = {
        "total_requirements":   total,
        "covered_full":         cnt_full,
        "covered_partial":      cnt_partial,
        "uncovered":            cnt_none,
        "coverage_pct":         _pct(cnt_full),
        "any_coverage_pct":     _pct(cnt_full + cnt_partial),
        "negative_coverage_pct": _pct(cnt_negative),
        "he_coverage_pct":      _pct(cnt_he),
        "mobile_coverage_pct":  _pct(cnt_mobile),
        "android_coverage_pct": _pct(cnt_android),
        "flaky_count":          len(flaky_requirements),
        "high_risk_count":      sum(1 for r in coverage_records if r["risk_level"] == "HIGH"),
        "medium_risk_count":    sum(1 for r in coverage_records if r["risk_level"] == "MEDIUM"),
        "missing_scenario_types": sum(len(m["missing"]) for m in missing_all),
    }

    # Feature rollup
    feature_rollup: dict[str, dict] = {}
    for r in coverage_records:
        feat = r["feature"]
        if feat not in feature_rollup:
            feature_rollup[feat] = {
                "feature":     feat,
                "criticality": FEATURE_CRITICALITY.get(feat, "MEDIUM"),
                "total":       0, "covered": 0, "partial": 0, "none": 0,
                "failed": 0, "flaky": 0,
            }
        fr = feature_rollup[feat]
        fr["total"] += 1
        if r["coverage_status"] == "FULL":    fr["covered"] += 1
        elif r["coverage_status"] == "PARTIAL": fr["partial"] += 1
        else:                                   fr["none"]    += 1
        if r["execution_status"]["failed"] > 0: fr["failed"]  += 1
        if r["flaky"]:                          fr["flaky"]   += 1

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary":       summary,
        "requirements":  coverage_records,
        "feature_rollup": sorted(feature_rollup.values(), key=lambda x: x["total"], reverse=True),
    }

    Path("reports/coverage_report.json").write_text(
        json.dumps(output, indent=2) + "\n", encoding="utf-8"
    )
    Path("reports/missing_scenarios.json").write_text(
        json.dumps({"generated_at": output["generated_at"], "missing": missing_all}, indent=2) + "\n",
        encoding="utf-8",
    )
    Path("reports/flaky_requirements.json").write_text(
        json.dumps({"generated_at": output["generated_at"], "flaky": flaky_requirements}, indent=2) + "\n",
        encoding="utf-8",
    )

    _write_markdown(coverage_records, summary, feature_rollup)

    print_stage_result("7b", "COVERAGE_ANALYSIS", {
        "Total requirements":     total,
        "Fully covered":          f"{cnt_full} ({summary['coverage_pct']}%)",
        "Partially covered":      cnt_partial,
        "Uncovered":              cnt_none,
        "Negative coverage":      f"{summary['negative_coverage_pct']}%",
        "HyperExecute coverage":  f"{summary['he_coverage_pct']}%",
        "Mobile coverage":        f"{summary['mobile_coverage_pct']}%",
        "Flaky requirements":     len(flaky_requirements),
        "High-risk requirements": summary["high_risk_count"],
        "Missing scenario types": summary["missing_scenario_types"],
        "Output":                 "reports/coverage_report.json",
    })
    return output


def _write_markdown(records: list, summary: dict, feature_rollup: dict) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Requirement Coverage Analysis Report",
        "",
        f"_Generated: {ts}_",
        "",
        "## Coverage Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Requirements | {summary['total_requirements']} |",
        f"| Fully Covered | {summary['covered_full']} ({summary['coverage_pct']}%) |",
        f"| Partially Covered | {summary['covered_partial']} |",
        f"| Uncovered | {summary['uncovered']} |",
        f"| Negative Test Coverage | {summary['negative_coverage_pct']}% |",
        f"| Mobile Coverage | {summary['mobile_coverage_pct']}% |",
        f"| Android Coverage | {summary['android_coverage_pct']}% |",
        f"| HyperExecute Coverage | {summary['he_coverage_pct']}% |",
        f"| Flaky Requirements | {summary['flaky_count']} |",
        f"| High-Risk Requirements | {summary['high_risk_count']} |",
        f"| Missing Scenario Types | {summary['missing_scenario_types']} |",
        "",
        "## Feature Coverage Heatmap",
        "",
        "| Feature | Criticality | Total | Covered | Partial | Uncovered | Failed | Flaky |",
        "|---------|-------------|-------|---------|---------|-----------|--------|-------|",
    ]
    for f in feature_rollup.values():
        lines.append(
            f"| {f['feature']} | {f['criticality']} | {f['total']} | "
            f"{f['covered']} | {f['partial']} | {f['none']} | {f['failed']} | {f['flaky']} |"
        )

    lines += [
        "",
        "## Requirement Coverage Detail",
        "",
        "| Requirement | Coverage | Tests | Pass | Fail | Missing | Risk |",
        "|-------------|----------|-------|------|------|---------|------|",
    ]
    for r in records:
        es = r["execution_status"]
        lines.append(
            f"| `{r['requirement_id']}` | {r['coverage_status']} | "
            f"{es['total']} | {es['passed']} | {es['failed']} | "
            f"{len(r['missing_scenarios'])} | {r['risk_level']} |"
        )

    lines += ["", "## Per-Requirement Detail", ""]
    for r in records:
        cats = r["coverage_categories"]
        es   = r["execution_status"]
        yes  = lambda v: "✅" if v else "❌"
        lines += [
            f"### {r['requirement_id']} — {r['feature']}",
            "",
            f"> {r['description']}",
            "",
            f"- **Coverage Status:** {r['coverage_status']}  |  **Risk:** {r['risk_level']}  "
            f"|  **Criticality:** {r['criticality']}  |  **Kane:** {r['kane_status']}",
            f"- **Functional Coverage:** {r['functional_coverage_pct']}%  "
            f"|  **Negative Coverage:** {r['negative_coverage_pct']}%",
            f"- **Browsers Tested:** {', '.join(r['browsers_tested']) or 'none'}",
            f"- **Flaky:** {'YES — ' + str(r['retry_count']) + ' retries' if r['flaky'] else 'no'}",
            "",
            "**Coverage Categories:**",
            f"| Happy Path | Negative | Edge Case | Mobile | Android | HyperExecute | Regression |",
            f"|------------|----------|-----------|--------|---------|--------------|------------|",
            f"| {yes(cats['happy_path'])} | {yes(cats['negative'])} | {yes(cats['edge_case'])} "
            f"| {yes(cats['mobile'])} | {yes(cats['android'])} | {yes(cats['he_executed'])} "
            f"| {yes(cats['regression'])} |",
            "",
            f"**Execution:** {es['total']} total | {es['passed']} passed | "
            f"{es['failed']} failed | {es['flaky']} flaky",
            "",
        ]
        if r["missing_scenarios"]:
            lines.append("**Missing Scenario Types:**")
            for ms in r["missing_scenarios"]:
                lines.append(f"- `[{ms['type'].upper()}]` {ms['description']}")
            lines.append("")
        if r["covered_scenarios"]:
            lines.append(f"**Covered by:** {', '.join(r['covered_scenarios'])}")
            lines.append("")

    lines += ["---", "_Coverage analysis generated by Agentic STLC pipeline_", ""]
    Path("reports/coverage_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    analyze()
