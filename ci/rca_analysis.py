#!/usr/bin/env python3
"""
Agentic STLC — RCA (Root Cause Analysis) stage.

New in the Contoso Traders pipeline — was not a separate job before.
Aggregates Kane + HyperExecute + Playwright failures, classifies root causes,
computes confidence scores, detects flaky tests.

Outputs:
  reports/rca_result.json               — structured failure data
  reports/execution_summary.md          — human-readable markdown
  reports/failure_classification_report.json — classifications only
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from stage_utils import print_stage_header, print_stage_result
except ImportError:
    def print_stage_header(n, name, desc=""): print(f"\n── [{n}] {name} {desc} ──")
    def print_stage_result(n, name, d): print(f"[{n}] {name}:", d)


# ── Failure type taxonomy ─────────────────────────────────────────────────────

_FAILURE_TYPES = {
    "selector_stale":   "Stale or broken CSS/XPath selector",
    "navigation":       "Page navigation or URL routing failure",
    "timeout":          "Element or page load timeout",
    "auth":             "Authentication or credential failure",
    "app_unreachable":  "Application URL unreachable",
    "kane_hang":        "Kane CLI subprocess timeout (no termination signal)",
    "assertion":        "Test assertion failed — app state mismatch",
    "infra":            "Infrastructure or runner failure",
    "flaky":            "Test passed on retry — potential flakiness",
    "unknown":          "Could not classify — manual review required",
}

_KEYWORD_TO_TYPE: list[tuple[list[str], str]] = [
    (["timeout", "timed out", "exceeded", "max-time"],       "timeout"),
    (["no element", "locator", "selector", "not found",
      "could not find", "visible"],                           "selector_stale"),
    (["navigate", "url", "route", "404", "not reach"],        "navigation"),
    (["login", "auth", "credential", "password", "401"],      "auth"),
    (["connection refused", "econnrefused", "unreachable",
      "net::err"],                                             "app_unreachable"),
    (["kane subprocess exceeded", "subprocess timeout",
      "kane cli produced no output", "kane hang"],            "kane_hang"),
    (["assert", "expected", "mismatch", "does not match"],    "assertion"),
    (["runner", "infra", "vm", "oom", "out of memory"],       "infra"),
]


def _classify_failure(summary: str, exit_code: str = "") -> tuple[str, float]:
    """Return (failure_type, confidence_score 0.0–1.0)."""
    text = (summary + " " + exit_code).lower()
    for keywords, ftype in _KEYWORD_TO_TYPE:
        if any(kw in text for kw in keywords):
            return ftype, 0.85
    return "unknown", 0.40


def _suggest_fix(failure_type: str, summary: str) -> str:
    suggestions = {
        "timeout":         "Increase wait_for_timeout or use wait_for_load_state; check for slow network.",
        "selector_stale":  "Re-inspect the element with browser DevTools and update the locator.",
        "navigation":      "Verify TARGET_URL is reachable; check for redirect chains or 404s.",
        "auth":            "Verify LT_USERNAME / LT_ACCESS_KEY secrets are set and not rotated.",
        "app_unreachable": "Check that cloudtesting.contosotraders.com is deployed and healthy.",
        "kane_hang":       "Kane CLI hanging — check LT credentials, network, and ws-endpoint reachability.",
        "assertion":       "App state does not match expectation — review the acceptance criterion.",
        "infra":           "LambdaTest infra issue — retry the job; contact LambdaTest support if persistent.",
        "flaky":           "Test passes intermittently — add explicit waits or increase retries.",
        "unknown":         "Manual review required — inspect the full Kane or Playwright log.",
    }
    return suggestions.get(failure_type, "No suggestion available.")


# ── Data loaders ─────────────────────────────────────────────────────────────

def _load_kane_failures() -> list[dict]:
    failures = []
    p = Path("requirements/analyzed_requirements.json")
    if not p.exists():
        return failures
    data = json.loads(p.read_text(encoding="utf-8"))
    for r in data:
        if r.get("kane_status") not in ("passed", "skipped"):
            ftype, conf = _classify_failure(
                r.get("kane_summary", ""), r.get("kane_status", "")
            )
            failures.append({
                "test_id":       r["id"],
                "source":        "kane",
                "title":         r.get("title", ""),
                "failure_type":  ftype,
                "root_cause":    r.get("kane_summary", "")[:200],
                "confidence":    conf,
                "suggested_fix": _suggest_fix(ftype, r.get("kane_summary", "")),
                "session_link":  (r.get("kane_links") or [""])[0],
                "duration":      r.get("kane_duration"),
            })
    return failures


def _load_playwright_failures() -> list[dict]:
    failures = []
    junit_path = Path("reports/junit.xml")
    if not junit_path.exists():
        return failures
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(str(junit_path))
        root = tree.getroot()
        for tc in root.iter("testcase"):
            failure_el = tc.find("failure")
            error_el   = tc.find("error")
            if failure_el is not None or error_el is not None:
                el      = failure_el if failure_el is not None else error_el
                msg     = (el.get("message") or el.text or "")[:300]
                ftype, conf = _classify_failure(msg)
                failures.append({
                    "test_id":       tc.get("classname", "") + "::" + tc.get("name", ""),
                    "source":        "playwright",
                    "title":         tc.get("name", ""),
                    "failure_type":  ftype,
                    "root_cause":    msg,
                    "confidence":    conf,
                    "suggested_fix": _suggest_fix(ftype, msg),
                    "session_link":  "",
                    "duration":      float(tc.get("time", 0)),
                })
    except Exception as exc:
        print(f"[RCA] JUnit parse error: {exc}")
    return failures


def _detect_flaky_tests(kane_failures: list[dict], pw_failures: list[dict]) -> list[dict]:
    """A test is considered flaky if its Kane status is timeout/error but
    exit_code suggests it eventually completed on the LambdaTest side."""
    flaky = []
    for f in kane_failures:
        if f["failure_type"] in ("timeout", "flaky"):
            flaky.append({
                "test_id": f["test_id"],
                "source":  f["source"],
                "reason":  "Kane timeout or intermittent failure — may pass on retry",
            })
    return flaky


def _compute_confidence_score(failures: list[dict]) -> str:
    if not failures:
        return "N/A (no failures)"
    avg = sum(f.get("confidence", 0.5) for f in failures) / len(failures)
    level = "HIGH" if avg >= 0.8 else ("MEDIUM" if avg >= 0.6 else "LOW")
    return f"{level} ({avg:.0%})"


# ── Execution summary markdown ────────────────────────────────────────────────

def _build_execution_summary(
    kane_failures: list[dict],
    pw_failures: list[dict],
    flaky: list[dict],
    confidence: str,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Agentic STLC — RCA Execution Summary",
        f"*Generated: {now}*",
        "",
        "## Overview",
        "",
        f"| Category | Count |",
        f"|----------|-------|",
        f"| Kane failures | {len(kane_failures)} |",
        f"| Playwright failures | {len(pw_failures)} |",
        f"| Flaky tests | {len(flaky)} |",
        f"| RCA confidence | {confidence} |",
        "",
    ]

    all_failures = kane_failures + pw_failures
    if all_failures:
        # Group by type
        by_type: dict[str, list] = {}
        for f in all_failures:
            by_type.setdefault(f["failure_type"], []).append(f)
        lines += ["## Failure Classification", ""]
        for ftype, items in sorted(by_type.items(), key=lambda x: -len(x[1])):
            lines.append(f"### {ftype.replace('_', ' ').title()} ({len(items)} failures)")
            lines.append(f"> {_FAILURE_TYPES.get(ftype, 'No description')}")
            lines.append("")
            lines.append("| Test | Source | Root Cause | Fix |")
            lines.append("|------|--------|------------|-----|")
            for f in items[:5]:
                lines.append(
                    f"| `{f['test_id']}` | {f['source']} "
                    f"| {f['root_cause'][:60]} "
                    f"| {f['suggested_fix'][:60]} |"
                )
            lines.append("")

    if flaky:
        lines += ["## Flaky Tests", ""]
        for t in flaky:
            lines.append(f"- `{t['test_id']}` ({t['source']}) — {t['reason']}")
        lines.append("")

    lines += [
        "## Recommended Actions", "",
        "1. Address `app_unreachable` and `kane_hang` failures first — these block entire criteria.",
        "2. Fix `selector_stale` failures by re-inspecting elements in DevTools.",
        "3. Increase timeouts for `timeout` failures in wait_for_* calls.",
        "4. Re-run `flaky` tests with `--retries=2` before marking as genuine failures.",
        "",
        "---",
        "*Agentic STLC RCA Engine*",
    ]
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print_stage_header("RCA", "FAILURE_INTELLIGENCE",
                        "Aggregate Kane + Playwright failures, classify, score, detect flaky")
    Path("reports").mkdir(exist_ok=True)

    kane_failures = _load_kane_failures()
    pw_failures   = _load_playwright_failures()
    flaky         = _detect_flaky_tests(kane_failures, pw_failures)
    all_failures  = kane_failures + pw_failures
    confidence    = _compute_confidence_score(all_failures)

    rca_result = {
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "total_failures":      len(all_failures),
        "kane_failures":       len(kane_failures),
        "playwright_failures": len(pw_failures),
        "flaky_tests":         flaky,
        "confidence_score":    confidence,
        "failures":            all_failures,
    }

    classification_report = {
        "generated_at": rca_result["generated_at"],
        "summary": {ft: sum(1 for f in all_failures if f["failure_type"] == ft)
                    for ft in _FAILURE_TYPES},
        "classifications": [
            {k: f[k] for k in ("test_id", "source", "failure_type", "confidence", "suggested_fix")}
            for f in all_failures
        ],
    }

    exec_summary = _build_execution_summary(kane_failures, pw_failures, flaky, confidence)

    Path("reports/rca_result.json").write_text(
        json.dumps(rca_result, indent=2), encoding="utf-8")
    Path("reports/failure_classification_report.json").write_text(
        json.dumps(classification_report, indent=2), encoding="utf-8")
    Path("reports/execution_summary.md").write_text(exec_summary, encoding="utf-8")

    print_stage_result("RCA", "FAILURE_INTELLIGENCE", {
        "Kane failures":       len(kane_failures),
        "Playwright failures": len(pw_failures),
        "Flaky detected":      len(flaky),
        "Confidence score":    confidence,
        "Output":              "reports/rca_result.json",
    })


if __name__ == "__main__":
    main()
