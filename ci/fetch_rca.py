"""
Root Cause Analysis fetcher — LambdaTest Insights RCA API.

For every FAILED test result in reports/normalized_results.json,
calls the LambdaTest RCA API to retrieve AI-generated root cause analysis
and publishes the findings to reports/rca_report.json + reports/rca_report.md.

API reference:
  GET https://api.lambdatest.com/insights/api/v3/public/rca
  Authorization: Basic base64(LT_USERNAME:LT_ACCESS_KEY)
  Query param:   session_id=<testID>   (extracted from session_link URL)

The session_link format from normalized_results.json is:
  https://automation.lambdatest.com/test?testID=OODBF-532VM-PBJHX-UC1R7

Environment variables:
  LT_USERNAME     — LambdaTest account username (required)
  LT_ACCESS_KEY   — LambdaTest access key (required)
  RCA_MAX_FAILED  — cap how many failed tests to query (default 20)
  RCA_TIMEOUT_S   — per-request timeout in seconds (default 30)

Sources:
  - reports/normalized_results.json
  - reports/traceability_matrix.json   (for requirement context)

Produces:
  - reports/rca_report.json
  - reports/rca_report.md
"""
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

try:
    import urllib.request
    import urllib.error
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result

# ── Config ────────────────────────────────────────────────────────────────────
LT_USERNAME   = os.environ.get("LT_USERNAME",   "")
LT_ACCESS_KEY = os.environ.get("LT_ACCESS_KEY", "")
RCA_API_BASE  = "https://api.lambdatest.com/insights/api/v3/public/rca"
RCA_MAX       = int(os.environ.get("RCA_MAX_FAILED", "20"))
RCA_TIMEOUT   = int(os.environ.get("RCA_TIMEOUT_S",   "30"))


def _load_json(path: str, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _auth_header() -> str:
    token = base64.b64encode(f"{LT_USERNAME}:{LT_ACCESS_KEY}".encode()).decode()
    return f"Basic {token}"


def _extract_test_id(session_link: str) -> str:
    """Extract testID from https://automation.lambdatest.com/test?testID=XXXX."""
    if not session_link:
        return ""
    try:
        qs = parse_qs(urlparse(session_link).query)
        return (qs.get("testID") or qs.get("test_id") or [""])[0]
    except Exception:
        return ""


def _call_rca_api_httpx(session_id: str) -> dict:
    """Fetch RCA using httpx."""
    import httpx
    url = f"{RCA_API_BASE}?session_id={session_id}"
    with httpx.Client(timeout=RCA_TIMEOUT) as client:
        resp = client.get(url, headers={"Authorization": _auth_header()})
        resp.raise_for_status()
        return resp.json()


def _call_rca_api_urllib(session_id: str) -> dict:
    """Fetch RCA using stdlib urllib (fallback when httpx absent)."""
    import urllib.request
    url = f"{RCA_API_BASE}?session_id={session_id}"
    req = urllib.request.Request(url, headers={"Authorization": _auth_header()})
    with urllib.request.urlopen(req, timeout=RCA_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _call_rca(session_id: str) -> dict:
    """Call RCA API with automatic http-client selection and error handling."""
    if not LT_USERNAME or not LT_ACCESS_KEY:
        return {"error": "LT_USERNAME or LT_ACCESS_KEY not set", "skipped": True}
    try:
        if _HAS_HTTPX:
            return _call_rca_api_httpx(session_id)
        if _HAS_URLLIB:
            return _call_rca_api_urllib(session_id)
        return {"error": "No HTTP client available (install httpx)", "skipped": True}
    except Exception as exc:
        return {"error": str(exc), "skipped": False}


def _extract_rca_summary(api_response: dict) -> str:
    """Pull a human-readable root-cause string from the API response."""
    if api_response.get("skipped"):
        return api_response.get("error", "skipped")
    if api_response.get("error"):
        return f"API error: {api_response['error']}"

    # Common LambdaTest RCA response shapes
    for key in ("root_cause", "rootCause", "rca", "summary", "description",
                "failure_reason", "failureReason", "message"):
        val = api_response.get(key)
        if val and isinstance(val, str):
            return val.strip()

    # Nested structures
    data = api_response.get("data") or api_response.get("result") or {}
    if isinstance(data, dict):
        for key in ("root_cause", "rootCause", "rca", "summary", "message"):
            val = data.get(key)
            if val and isinstance(val, str):
                return val.strip()

    # Last resort: return first non-trivial string value found
    for v in api_response.values():
        if isinstance(v, str) and len(v) > 20:
            return v.strip()

    return json.dumps(api_response)[:300]


def _build_req_context(
    scenario_id:    str,
    requirement_id: str,
    trace_rows:     list[dict],
) -> dict:
    """Look up Kane AI context for the requirement being analyzed."""
    row = next((r for r in trace_rows if r.get("scenario_id") == scenario_id), {})
    return {
        "requirement_id":     requirement_id,
        "acceptance_criterion": row.get("acceptance_criterion", ""),
        "kane_status":        row.get("kane_ai_result", "unknown"),
        "kane_one_liner":     row.get("kane_one_liner", ""),
    }


def fetch_rca(
    normalized_path: str = "reports/normalized_results.json",
    trace_path:      str = "reports/traceability_matrix.json",
) -> dict:
    print_stage_header("7e", "FETCH_RCA",
                        "Fetch LambdaTest AI Root Cause Analysis for failed tests")
    Path("reports").mkdir(exist_ok=True)

    norm_raw    = _load_json(normalized_path, {})
    normalized  = norm_raw.get("results", [])
    trace_data  = _load_json(trace_path, {})
    trace_rows  = trace_data.get("rows", [])

    # Collect unique failed tests with a session link
    failed_tests: list[dict] = []
    seen_sessions: set = set()
    for r in normalized:
        if r.get("status") != "failed":
            continue
        link = r.get("session_link", "")
        test_id = _extract_test_id(link)
        if not test_id or test_id in seen_sessions:
            continue
        seen_sessions.add(test_id)
        failed_tests.append({
            "test_id":        test_id,
            "session_link":   link,
            "scenario_id":    r.get("scenario_id", ""),
            "requirement_id": r.get("requirement_id", ""),
            "browser":        r.get("browser", ""),
            "function_name":  r.get("function_name", ""),
            "error_message":  r.get("error_message", ""),
        })

    if not failed_tests:
        print("[fetch_rca] No failed tests with session links found — nothing to analyze")
        result = {
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "total_failed":  0,
            "rca_fetched":   0,
            "analyses":      [],
        }
        _write_outputs(result)
        print_stage_result("7e", "FETCH_RCA", {
            "Failed tests":    0,
            "RCA analyses":    0,
            "Output":          "reports/rca_report.json",
        })
        return result

    capped = failed_tests[:RCA_MAX]
    analyses: list[dict] = []
    rca_fetched = 0

    for ft in capped:
        print(f"[fetch_rca] Querying RCA for {ft['scenario_id']} / {ft['browser']} "
              f"— session {ft['test_id']}")

        api_resp = _call_rca(ft["test_id"])
        rca_text = _extract_rca_summary(api_resp)
        ctx      = _build_req_context(ft["scenario_id"], ft["requirement_id"], trace_rows)

        analysis = {
            "scenario_id":          ft["scenario_id"],
            "requirement_id":       ft["requirement_id"],
            "browser":              ft["browser"],
            "function_name":        ft["function_name"],
            "session_link":         ft["session_link"],
            "test_id":              ft["test_id"],
            "error_message":        ft["error_message"],
            "root_cause":           rca_text,
            "kane_context":         ctx,
            "raw_api_response":     api_resp if not api_resp.get("skipped") else {},
        }
        analyses.append(analysis)

        if not api_resp.get("skipped") and not api_resp.get("error"):
            rca_fetched += 1

        # Polite rate limiting — avoid overwhelming the API
        if len(capped) > 1:
            time.sleep(0.5)

    result = {
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "total_failed":        len(failed_tests),
        "queried":             len(capped),
        "rca_fetched":         rca_fetched,
        "skipped_no_creds":    not bool(LT_USERNAME and LT_ACCESS_KEY),
        "analyses":            analyses,
    }

    _write_outputs(result)

    print_stage_result("7e", "FETCH_RCA", {
        "Failed tests found": len(failed_tests),
        "RCA queries made":   len(capped),
        "RCA analyses":       rca_fetched,
        "Creds present":      bool(LT_USERNAME and LT_ACCESS_KEY),
        "Output":             "reports/rca_report.json, reports/rca_report.md",
    })
    return result


def _write_outputs(result: dict) -> None:
    Path("reports/rca_report.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    _write_markdown(result)


def _write_markdown(result: dict) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Root Cause Analysis Report",
        "",
        f"_Generated: {ts}_",
        "",
        f"- **Failed tests found:** {result['total_failed']}",
        f"- **RCA queries made:** {result.get('queried', 0)}",
        f"- **RCA analyses retrieved:** {result['rca_fetched']}",
        "",
    ]

    if result.get("skipped_no_creds"):
        lines += [
            "> ⚠️ **LT_USERNAME / LT_ACCESS_KEY not set** — RCA API calls were skipped.",
            "> Set these secrets in GitHub Actions or your local environment to enable RCA.",
            "",
        ]

    analyses = result.get("analyses", [])
    if not analyses:
        lines += ["_No failures to analyze._", ""]
    else:
        lines += [
            "## Failure Root Cause Analyses",
            "",
        ]
        for a in analyses:
            req_id  = a.get("requirement_id", "?")
            sc_id   = a.get("scenario_id",    "?")
            browser = a.get("browser",         "?")
            link    = a.get("session_link",    "")
            rca     = a.get("root_cause",      "N/A")
            err     = a.get("error_message",   "")
            ctx     = a.get("kane_context",    {})

            session_md = f"[View session]({link})" if link else "—"
            lines += [
                f"### ❌ {sc_id} / {req_id} — {browser}",
                "",
                f"- **Session:** {session_md}",
                f"- **Function:** `{a.get('function_name', '?')}`",
            ]
            if ctx.get("acceptance_criterion"):
                lines.append(f"- **Requirement:** {ctx['acceptance_criterion']}")
            if ctx.get("kane_status"):
                lines.append(f"- **Kane AI result:** {ctx['kane_status']}"
                             + (f" — {ctx['kane_one_liner']}" if ctx.get("kane_one_liner") else ""))
            lines += [
                "",
                "**Error Message:**",
                f"```",
                (err or "No error message captured")[:600],
                "```",
                "",
                "**Root Cause Analysis:**",
                f"> {rca}",
                "",
            ]

    lines += ["---", "_RCA generated by Agentic STLC pipeline via LambdaTest Insights API_", ""]
    Path("reports/rca_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    fetch_rca()
