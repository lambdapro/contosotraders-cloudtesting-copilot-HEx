"""
Kane AI RCA fetcher — AI-Powered Root Cause Analysis for Kane functional sessions.

For every Kane AI session in requirements/analyzed_requirements.json this calls
the LambdaTest AI RCA API and publishes actionable fix guidance that Claude (or
Copilot) can consume directly to repair the application under test.

API (official):
  GET https://api.lambdatest.com/insights/api/v3/public/rca   (US)
  GET https://eu-api.lambdatest.com/insights/api/v3/public/rca (EU)
  Auth:   Authorization: Basic base64(LT_USERNAME:LT_ACCESS_KEY)
  Params: test_ids=<comma-separated testIDs>   (also supports job_ids/task_ids/
          stage_ids), page, limit
  Doc:    https://www.testmuai.com/support/api-doc/analytics/root-cause-analysis/
          get-ai-powered-root-cause-analysis-for-test-failures/

Response data[].rca_detail carries the fields Claude needs to act:
  root_cause_category, parent_failure_category, failure_summary, stack_trace,
  root_cause_failure_stack_trace, analysis[], steps_to_fix[{issue, module,
  suggested_fix}], error_timeline[{step_name, timestamp, source_log, summary}]

Kane sessions run on the LambdaTest grid and expose a real Automation testID via
their session link (https://automation.lambdatest.com/test?testID=XXXX). That
testID is what the RCA API keys on.

Environment variables:
  LT_USERNAME        LambdaTest username (required)
  LT_ACCESS_KEY      LambdaTest access key (required)
  LT_REGION          "eu" to use the EU host (default US)
  KANE_RCA_SCOPE     "failed" (default — failed+timeout), or "all"
  KANE_RCA_TIMEOUT_S per-request timeout seconds (default 30)

Inputs:
  requirements/analyzed_requirements.json   (Kane results + session links)

Outputs:
  reports/kane_rca.json   machine-readable: per requirement + raw rca_detail
  reports/kane_rca.md     Claude-readable "Fix Intelligence" (steps_to_fix first)
"""
import base64
import json
import os
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

import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).parent))
try:
    from stage_utils import print_stage_header, print_stage_result
except Exception:  # stage_utils optional — keep the tool self-contained
    def print_stage_header(*a, **k): print(f"[kane_rca] {' '.join(str(x) for x in a)}")
    def print_stage_result(*a, **k): print(f"[kane_rca] done: {k or a}")

# ── Config ──────────────────────────────────────────────────────────────────
LT_USERNAME   = os.environ.get("LT_USERNAME", "")
LT_ACCESS_KEY = os.environ.get("LT_ACCESS_KEY", "")
_REGION       = os.environ.get("LT_REGION", "").lower()
RCA_HOST      = "https://eu-api.lambdatest.com" if _REGION == "eu" else "https://api.lambdatest.com"
RCA_API       = f"{RCA_HOST}/insights/api/v3/public/rca"
RCA_SCOPE     = os.environ.get("KANE_RCA_SCOPE", "failed").lower()
RCA_TIMEOUT   = int(os.environ.get("KANE_RCA_TIMEOUT_S", "30"))
RCA_BATCH     = 25  # test_ids per request


def _load_json(path: str, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[kane_rca] WARN: could not parse {path}: {exc}")
        return default


def _auth_header() -> str:
    return "Basic " + base64.b64encode(f"{LT_USERNAME}:{LT_ACCESS_KEY}".encode()).decode()


def _extract_test_id(*candidates: str) -> str:
    """Pull an Automation testID from any automation.lambdatest.com link, or accept
    a bare testID. Returns "" if none can be derived."""
    for c in candidates:
        if not c:
            continue
        if "automation.lambdatest.com" in c:
            qs = parse_qs(urlparse(c).query)
            tid = (qs.get("testID") or qs.get("test_id") or [""])[0]
            if tid:
                return tid
    # bare id fallback (kane_session_id may already be the testID)
    for c in candidates:
        if c and "://" not in c and "/" not in c and len(c) >= 8:
            return c
    return ""


def fetch_rca_params(params: dict) -> dict:
    """GET RCA for any combination of id params the API accepts:
    test_ids / job_ids / task_ids / stage_ids (comma-separated strings), plus
    optional page / limit. Returns the parsed JSON or an error dict."""
    if not (LT_USERNAME and LT_ACCESS_KEY):
        return {"error": "LT_USERNAME / LT_ACCESS_KEY not set", "skipped": True}
    query = {k: v for k, v in params.items() if v}
    if not any(query.get(k) for k in ("test_ids", "job_ids", "task_ids", "stage_ids")):
        return {"data": []}
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    url = f"{RCA_API}?{qs}"
    headers = {"Authorization": _auth_header()}
    try:
        if _HAS_HTTPX:
            with httpx.Client(timeout=RCA_TIMEOUT) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=RCA_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        return {"error": str(exc), "skipped": False}


def _call_rca(test_ids: list[str]) -> dict:
    """GET RCA for a batch of testIDs (Kane flow)."""
    if not test_ids:
        return {"data": []}
    return fetch_rca_params({"test_ids": ",".join(test_ids), "limit": len(test_ids)})


def _index_rca_by_test_id(api_resp: dict) -> dict:
    """Map test_id -> rca record from an API response."""
    out = {}
    for rec in (api_resp.get("data") or []):
        tid = rec.get("test_id") or ""
        if tid:
            out[tid] = rec
    return out


def fetch_kane_rca(analyzed_path: str = "requirements/analyzed_requirements.json") -> dict:
    print_stage_header("7f", "KANE_RCA",
                       "Fetch AI Root Cause Analysis for Kane AI sessions")
    Path("reports").mkdir(exist_ok=True)

    analyzed = _load_json(analyzed_path, [])
    if isinstance(analyzed, dict):  # tolerate {"requirements": [...]} shape
        analyzed = analyzed.get("requirements") or analyzed.get("data") or []

    # Pick the Kane sessions to analyze
    def _wanted(rec) -> bool:
        st = str(rec.get("kane_status", "")).lower()
        return True if RCA_SCOPE == "all" else st in ("failed", "fail", "timeout", "error")

    targets, no_testid = [], []
    for rec in analyzed:
        if not _wanted(rec):
            continue
        links = rec.get("kane_links") or []
        tid = _extract_test_id(
            rec.get("kane_session_id", ""),
            *links,
        )
        entry = {
            "requirement_id": rec.get("id", ""),
            "title":          rec.get("title", ""),
            "description":    rec.get("description", ""),
            "kane_status":    rec.get("kane_status", ""),
            "kane_one_liner": rec.get("kane_one_liner", ""),
            "kane_steps":     rec.get("kane_steps", []),
            "kane_links":     links,
            "test_id":        tid,
        }
        (targets if tid else no_testid).append(entry)

    # Batch-query the RCA API
    rca_by_tid: dict = {}
    api_errors: list[str] = []
    skipped_creds = not (LT_USERNAME and LT_ACCESS_KEY)
    all_tids = [t["test_id"] for t in targets]
    for i in range(0, len(all_tids), RCA_BATCH):
        batch = all_tids[i:i + RCA_BATCH]
        print(f"[kane_rca] querying RCA for {len(batch)} session(s): {', '.join(batch)}")
        resp = _call_rca(batch)
        if resp.get("error"):
            api_errors.append(resp["error"])
        rca_by_tid.update(_index_rca_by_test_id(resp))
        if len(all_tids) > RCA_BATCH:
            time.sleep(0.5)

    # Stitch RCA back onto each requirement
    analyses = []
    for t in targets:
        rec = rca_by_tid.get(t["test_id"], {})
        t["rca_category"] = rec.get("rca_category", "")
        t["rca_detail"]   = rec.get("rca_detail", {}) or {}
        t["rca_found"]    = bool(rec)
        analyses.append(t)

    result = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "api":            RCA_API,
        "scope":          RCA_SCOPE,
        "sessions_total": len(targets) + len(no_testid),
        "with_test_id":   len(targets),
        "without_test_id": len(no_testid),
        "rca_found":      sum(1 for a in analyses if a["rca_found"]),
        "skipped_no_creds": skipped_creds,
        "api_errors":     api_errors,
        "analyses":       analyses,
        "no_test_id":     no_testid,
    }
    _write_outputs(result)

    print_stage_result("7f", "KANE_RCA", {
        "Kane sessions":   len(targets) + len(no_testid),
        "With testID":     len(targets),
        "RCA retrieved":   result["rca_found"],
        "Creds present":   not skipped_creds,
        "Output":          "reports/kane_rca.json, reports/kane_rca.md",
    })
    return result


def _write_outputs(result: dict) -> None:
    Path("reports/kane_rca.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    Path("reports/kane_rca.md").write_text(_render_md(result), encoding="utf-8")


def _render_md(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [
        "# Kane AI — Root Cause Analysis & Fix Intelligence",
        "",
        f"_Generated: {ts} · source: AI-Powered RCA API_",
        "",
        f"- **Kane sessions analyzed:** {result['with_test_id']}"
        f" (of {result['sessions_total']})",
        f"- **RCA retrieved:** {result['rca_found']}",
        f"- **Scope:** {result['scope']}",
        "",
    ]
    if result.get("skipped_no_creds"):
        L += ["> ⚠️ `LT_USERNAME` / `LT_ACCESS_KEY` not set — RCA API calls were skipped.", ""]
    if result.get("api_errors"):
        L += ["> ⚠️ API errors: " + "; ".join(dict.fromkeys(result["api_errors"]))[:300], ""]

    analyses = [a for a in result["analyses"] if a["rca_found"]] + \
               [a for a in result["analyses"] if not a["rca_found"]]
    if not analyses:
        L += ["_No Kane sessions matched the scope (no failures), or no testIDs were available._", ""]

    for a in analyses:
        d = a.get("rca_detail", {}) or {}
        rid = a.get("requirement_id", "?")
        L += [
            "---",
            f"## {rid} — {a.get('title','')}",
            "",
            f"- **Kane status:** `{a.get('kane_status','?')}`"
            + (f" — {a['kane_one_liner']}" if a.get("kane_one_liner") else ""),
            f"- **testID:** `{a.get('test_id','—')}`",
            f"- **RCA category:** {a.get('rca_category') or d.get('root_cause_category') or '—'}",
            "",
        ]
        if not a["rca_found"]:
            L += ["_No RCA record returned for this session (it may have passed, or RCA is still"
                  " generating)._", ""]
            continue

        if d.get("failure_summary"):
            L += ["**Failure summary**", "", f"> {d['failure_summary']}", ""]

        analysis = d.get("analysis") or []
        if analysis:
            L += ["**Analysis**", ""] + [f"- {x}" for x in analysis] + [""]

        # The actionable part for Claude/Copilot
        fixes = d.get("steps_to_fix") or []
        if fixes:
            L += ["**Steps to fix** (act on these)", "",
                  "| # | Module | Issue | Suggested fix |",
                  "|---|--------|-------|---------------|"]
            for i, f in enumerate(fixes, 1):
                if isinstance(f, dict):
                    mod = str(f.get("module", "")).replace("|", "\\|")
                    iss = str(f.get("issue", "")).replace("|", "\\|")
                    fix = str(f.get("suggested_fix", "")).replace("|", "\\|")
                else:
                    mod, iss, fix = "", "", str(f)
                L.append(f"| {i} | {mod} | {iss} | {fix} |")
            L += [""]

        timeline = d.get("error_timeline") or []
        if timeline:
            L += ["**Error timeline**", ""]
            for ev in timeline:
                if isinstance(ev, dict):
                    L.append(f"- `{ev.get('timestamp','')}` **{ev.get('step_name','')}**"
                             f" ({ev.get('source_log','')}): {ev.get('summary','')}")
            L += [""]

        stack = d.get("root_cause_failure_stack_trace") or d.get("stack_trace") or ""
        if stack:
            L += ["**Stack trace**", "", "```", stack[:1500], "```", ""]

        if a.get("kane_steps"):
            L += ["<details><summary>Kane steps</summary>", ""]
            L += [f"{i+1}. {s}" for i, s in enumerate(a["kane_steps"][:30])]
            L += ["", "</details>", ""]

    if result.get("no_test_id"):
        L += ["---", "## Sessions without an Automation testID",
              "_RCA needs a `automation.lambdatest.com/test?testID=` link; these Kane"
              " runs only produced share/testcase links:_", ""]
        for a in result["no_test_id"]:
            L.append(f"- {a.get('requirement_id','?')} — {a.get('title','')}"
                     f" (`{a.get('kane_status','?')}`)")
        L += [""]

    L += ["---", "_Kane RCA fetched by the Agentic STLC pipeline via the LambdaTest"
          " AI-Powered RCA API. Feed `steps_to_fix` to Claude/Copilot to drive"
          " application fixes — never edit the auto-generated test files._", ""]
    return "\n".join(L) + "\n"


def _adhoc(args) -> dict:
    """Direct RCA query embedding the full API surface (test/job/task/stage ids)."""
    params = {
        "test_ids":  args.test_ids,
        "job_ids":   args.job_ids,
        "task_ids":  args.task_ids,
        "stage_ids": args.stage_ids,
        "page":      args.page,
        "limit":     args.limit,
    }
    print(f"[kane_rca] ad-hoc RCA query: "
          + ", ".join(f"{k}={v}" for k, v in params.items() if v))
    resp = fetch_rca_params(params)
    data = resp.get("data", []) if isinstance(resp, dict) else []
    # Re-shape into the same analyses structure so the markdown renderer applies.
    analyses = []
    for rec in data:
        analyses.append({
            "requirement_id": rec.get("test_id") or rec.get("task_id") or rec.get("job_id", ""),
            "title":          f"job={rec.get('job_id','')} task={rec.get('task_id','')}",
            "kane_status":    "failed",
            "kane_one_liner": "",
            "kane_steps":     [],
            "kane_links":     [],
            "test_id":        rec.get("test_id", ""),
            "rca_category":   rec.get("rca_category", ""),
            "rca_detail":     rec.get("rca_detail", {}) or {},
            "rca_found":      True,
        })
    result = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "api":            RCA_API,
        "scope":          "adhoc",
        "sessions_total": len(analyses),
        "with_test_id":   len(analyses),
        "without_test_id": 0,
        "rca_found":      len(analyses),
        "skipped_no_creds": not (LT_USERNAME and LT_ACCESS_KEY),
        "api_errors":     [resp["error"]] if isinstance(resp, dict) and resp.get("error") else [],
        "analyses":       analyses,
        "no_test_id":     [],
    }
    _write_outputs(result)
    print(f"[kane_rca] ad-hoc: {len(analyses)} RCA record(s) → reports/kane_rca.json|md")
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Fetch AI-Powered RCA (LambdaTest insights API). "
                    "No id flags → Kane flow over requirements/analyzed_requirements.json.")
    ap.add_argument("--analyzed", default="requirements/analyzed_requirements.json",
                    help="Path to analyzed_requirements.json (Kane flow)")
    ap.add_argument("--test-ids",  dest="test_ids",  default="", help="comma-separated test IDs")
    ap.add_argument("--job-ids",   dest="job_ids",   default="", help="comma-separated HE job IDs")
    ap.add_argument("--task-ids",  dest="task_ids",  default="", help="comma-separated HE task IDs")
    ap.add_argument("--stage-ids", dest="stage_ids", default="", help="comma-separated HE stage IDs")
    ap.add_argument("--page",  default="", help="page number (default 1)")
    ap.add_argument("--limit", default="", help="records per page (default 10)")
    a = ap.parse_args()
    if any([a.test_ids, a.job_ids, a.task_ids, a.stage_ids]):
        _adhoc(a)
    else:
        fetch_kane_rca(a.analyzed)
