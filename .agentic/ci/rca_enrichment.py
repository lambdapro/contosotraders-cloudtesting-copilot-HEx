"""
Stage 8d — Enriched RCA via LambdaTest MCP + REST APIs.

Reads:
  reports/rca_report.json           — base RCA from fetch_rca.py
  reports/failure_intelligence.json — classification from failure_intelligence.py
  reports/normalized_results.json   — per-browser results for cross-browser correlation
  reports/api_details.json          — HE task_id per session

For each failed test, collects in parallel:
  - session metadata (video, screenshot, browser, OS, duration)
  - console JS errors
  - failed network requests (4xx/5xx)
  - HyperExecute categorized error data
  - MCP session details (if available)

Derives:
  - cross-browser failure scope (ALL_BROWSERS | SOME_BROWSERS | SINGLE_BROWSER | NO_DATA)
  - retry/rerun tagging
  - flakiness verdict (REPRODUCIBLE | LIKELY_FLAKY | POSSIBLY_FLAKY | POSSIBLY_ENVIRONMENT)
  - confidence score 0-100
  - enhanced deterministic root cause narrative
  - enriched remediation with artifact links

Writes:
  reports/rca_enriched.json
  reports/rca_enriched.md
  reports/rca_history.json  (append-only, last 50 runs)

Advisory — always returns 0, never blocks the pipeline.
"""
import asyncio
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False

# ── Config ─────────────────────────────────────────────────────────────────
LT_USERNAME     = os.environ.get("LT_USERNAME", "")
LT_ACCESS_KEY   = os.environ.get("LT_ACCESS_KEY", "")
RUN_NUMBER      = os.environ.get("GITHUB_RUN_NUMBER", "")
MCP_URL         = "https://mcp.lambdatest.com/mcp"
LT_API_BASE     = "https://api.lambdatest.com/automation/api/v1"
HE_API_BASE     = "https://api.hyperexecute.cloud"
MAX_CONCURRENT  = int(os.environ.get("RCA_MAX_CONCURRENT", "5"))
ARTIFACT_TIMEOUT = int(os.environ.get("RCA_ARTIFACT_TIMEOUT_S", "20"))
SCHEMA_VERSION  = "1.0"

# Base confidence scores by failure type
_BASE_CONFIDENCE = {
    "AUTH_PREREQUISITE_MISSING":    40,
    "PLAYWRIGHT_LOCATOR_FAILURE":   35,
    "PLAYWRIGHT_SYNC_TIMING":       35,
    "PLAYWRIGHT_NAVIGATION_FAILURE": 30,
    "APPLICATION_DEFECT":           30,
    "KANE_WRONG_TASK":              25,
    "KANE_STEP_LIMIT":              25,
    "DATA_UNAVAILABLE":             10,
    "UNKNOWN_FAILURE":               5,
}


# ── Helpers ─────────────────────────────────────────────────────────────────

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
    if not session_link:
        return ""
    try:
        qs = parse_qs(urlparse(session_link).query)
        return (qs.get("testID") or qs.get("test_id") or [""])[0]
    except Exception:
        return ""


def _load_input_artifacts() -> tuple:
    """Load all input files. Returns (failures, rca_analyses, normalized_results, he_tasks)."""
    fi = _load_json("reports/failure_intelligence.json", {})
    failures = fi.get("failures", []) if isinstance(fi, dict) else []

    rca = _load_json("reports/rca_report.json", {})
    rca_analyses = rca.get("analyses", []) if isinstance(rca, dict) else []

    norm = _load_json("reports/normalized_results.json", {})
    normalized = norm.get("results", []) if isinstance(norm, dict) else []

    api = _load_json("reports/api_details.json", {})
    he_tasks = api.get("he_tasks", []) if isinstance(api, dict) else []

    return failures, rca_analyses, normalized, he_tasks


def _build_rca_lookup(rca_analyses: list) -> dict:
    """Index rca_analyses by test_id and by session_link for quick lookup."""
    by_test_id = {}
    by_link = {}
    for a in rca_analyses:
        tid = a.get("test_id", "")
        if tid:
            by_test_id[tid] = a
        link = a.get("session_link", "")
        if link:
            by_link[link] = a
    return {"by_test_id": by_test_id, "by_link": by_link}


def _build_task_lookup(he_tasks: list) -> dict:
    """Index he_tasks by session_link for quick task_id lookup."""
    result = {}
    for t in he_tasks:
        link = t.get("session_link", "")
        if link:
            result[link] = t.get("task_id", "")
    return result


# ── REST artifact fetchers (all degrade to empty on failure) ─────────────────

async def _fetch_session_metadata(client, session_id: str) -> dict:
    if not session_id:
        return {}
    try:
        resp = await client.get(
            f"{LT_API_BASE}/sessions/{session_id}",
            headers={"Authorization": _auth_header()},
            timeout=ARTIFACT_TIMEOUT,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        d = data.get("data", data)
        return {
            "video_url":        d.get("video_url", ""),
            "screenshot_url":   d.get("screenshot_url", ""),
            "browser":          d.get("browser", ""),
            "browser_version":  d.get("browser_version", ""),
            "os":               d.get("os", ""),
            "duration_ms":      d.get("duration", 0),
        }
    except Exception:
        return {}


async def _fetch_console_log(client, session_id: str) -> list:
    if not session_id:
        return []
    try:
        resp = await client.get(
            f"{LT_API_BASE}/sessions/{session_id}/log/console",
            headers={"Authorization": _auth_header()},
            timeout=ARTIFACT_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        logs = data if isinstance(data, list) else data.get("data", data.get("logs", []))
        errors = []
        for entry in (logs if isinstance(logs, list) else []):
            if isinstance(entry, str):
                if any(kw in entry.lower() for kw in ("error", "exception", "uncaught", "failed")):
                    errors.append(entry[:300])
            elif isinstance(entry, dict):
                lvl = entry.get("level", "").lower()
                msg = entry.get("message", entry.get("text", ""))
                if lvl in ("error", "severe") or "error" in msg.lower():
                    errors.append(str(msg)[:300])
        return errors[:10]
    except Exception:
        return []


async def _fetch_network_log(client, session_id: str) -> list:
    if not session_id:
        return []
    try:
        resp = await client.get(
            f"{LT_API_BASE}/sessions/{session_id}/log/network",
            headers={"Authorization": _auth_header()},
            timeout=ARTIFACT_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        logs = data if isinstance(data, list) else data.get("data", data.get("logs", []))
        failed = []
        for entry in (logs if isinstance(logs, list) else []):
            if isinstance(entry, dict):
                status = entry.get("status", entry.get("statusCode", 0))
                try:
                    status_int = int(status)
                except (ValueError, TypeError):
                    status_int = 0
                if status_int >= 400:
                    failed.append({
                        "url":    entry.get("url", "")[:200],
                        "method": entry.get("method", "GET"),
                        "status": status_int,
                    })
        return failed[:10]
    except Exception:
        return []


async def _fetch_he_rca_categories(client, task_id: str) -> list:
    if not task_id:
        return []
    try:
        resp = await client.get(
            f"{HE_API_BASE}/v1.0/categorizederrors",
            params={"taskId": task_id, "order": "desc", "iteration": "1"},
            headers={"Authorization": _auth_header()},
            timeout=ARTIFACT_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        cats = data if isinstance(data, list) else data.get("data", data.get("categories", []))
        return [
            {
                "category":    c.get("category", c.get("name", "")),
                "count":       c.get("count", 1),
                "description": c.get("description", c.get("message", "")),
            }
            for c in (cats if isinstance(cats, list) else [])
        ][:5]
    except Exception:
        return []


async def _collect_session_evidence(client, session_id: str, task_id: str) -> dict:
    metadata, console_errors, network_errors, he_categories = await asyncio.gather(
        _fetch_session_metadata(client, session_id),
        _fetch_console_log(client, session_id),
        _fetch_network_log(client, session_id),
        _fetch_he_rca_categories(client, task_id),
        return_exceptions=False,
    )
    return {
        "session_id":     session_id,
        "task_id":        task_id,
        "metadata":       metadata,
        "console_errors": console_errors,
        "network_errors": network_errors,
        "he_categories":  he_categories,
    }


# ── MCP supplemental enrichment ──────────────────────────────────────────────

async def _fetch_mcp_session_details(mcp_session, session_id: str) -> dict:
    if mcp_session is None or not session_id:
        return {}
    try:
        tools_result = await mcp_session.list_tools()
        available = {t.name for t in (tools_result.tools or [])}
        tool_name = next(
            (t for t in ("getTestDetails", "getSessionDetails", "getAutomationSessionInfo") if t in available),
            None,
        )
        if not tool_name:
            return {}
        raw = await mcp_session.call_tool(tool_name, {"sessionId": session_id})
        text = raw.content[0].text if raw.content else "{}"
        # Strip markdown code fences if present
        import re
        text = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
        text = re.sub(r"\n?```$", "", text.strip())
        return json.loads(text)
    except Exception:
        return {}


# ── Derived signal computation ───────────────────────────────────────────────

def _detect_retry_runs(scenario_id: str, normalized_results: list) -> dict:
    by_browser: dict = {}
    for r in normalized_results:
        if r.get("scenario_id") != scenario_id:
            continue
        browser = r.get("browser", "unknown")
        link = r.get("session_link", "")
        if browser not in by_browser:
            by_browser[browser] = []
        if link and link not in by_browser[browser]:
            by_browser[browser].append(link)

    total_attempts = sum(len(v) for v in by_browser.values())
    retry_sessions = [lnk for links in by_browser.values() for lnk in links[1:]]
    primary_sessions = [links[0] for links in by_browser.values() if links]

    return {
        "total_attempts":  total_attempts,
        "unique_browsers": list(by_browser.keys()),
        "retry_sessions":  retry_sessions,
        "is_retry":        len(retry_sessions) > 0,
        "primary_sessions": primary_sessions,
    }


def _correlate_browsers(scenario_id: str, normalized_results: list) -> dict:
    browser_status: dict = {}
    for r in normalized_results:
        if r.get("scenario_id") != scenario_id:
            continue
        browser = r.get("browser", "unknown")
        status = r.get("status", "unknown")
        # A browser only counts as passed if ANY session for it passed
        if browser not in browser_status:
            browser_status[browser] = status
        elif status == "passed":
            browser_status[browser] = "passed"

    if not browser_status:
        return {
            "total_browsers_tested": 0, "total_browsers_failed": 0,
            "failed_browsers": [], "passed_browsers": [],
            "failure_scope": "NO_DATA", "is_systemic": False,
        }

    failed = [b for b, s in browser_status.items() if s != "passed"]
    passed = [b for b, s in browser_status.items() if s == "passed"]

    if len(failed) == len(browser_status):
        scope = "ALL_BROWSERS"
    elif len(failed) == 1:
        scope = "SINGLE_BROWSER"
    elif failed:
        scope = "SOME_BROWSERS"
    else:
        scope = "NO_DATA"

    return {
        "total_browsers_tested": len(browser_status),
        "total_browsers_failed": len(failed),
        "failed_browsers":       failed,
        "passed_browsers":       passed,
        "failure_scope":         scope,
        "is_systemic":           scope == "ALL_BROWSERS",
    }


def _assess_flakiness(retry_info: dict, browser_correlation: dict, he_categories: list, console_errors: list) -> dict:
    evidence = []
    verdict = "REPRODUCIBLE"

    # Retry with mixed outcomes → flaky
    if retry_info.get("is_retry") and retry_info.get("total_attempts", 0) > 1:
        verdict = "LIKELY_FLAKY"
        evidence.append(f"Retry detected: {retry_info['total_attempts']} total attempts")

    # HE categories with transient signals
    transient_keywords = ("timeout", "network", "connection", "transient", "flaky", "unstable", "intermittent")
    for cat in he_categories:
        desc = (cat.get("description", "") + " " + cat.get("category", "")).lower()
        if any(kw in desc for kw in transient_keywords):
            if verdict == "REPRODUCIBLE":
                verdict = "POSSIBLY_FLAKY"
            evidence.append(f"HE category: {cat.get('category', '')}")

    # Single-browser failure → possibly environment issue
    scope = browser_correlation.get("failure_scope", "NO_DATA")
    if scope == "SINGLE_BROWSER" and verdict == "REPRODUCIBLE":
        verdict = "POSSIBLY_ENVIRONMENT"
        failed = browser_correlation.get("failed_browsers", [])
        evidence.append(f"Only failed on {failed[0] if failed else 'one browser'}")

    if not evidence:
        evidence.append("No retries recorded, consistent across sessions")

    confidence = {
        "REPRODUCIBLE":          80,
        "POSSIBLY_ENVIRONMENT":  60,
        "POSSIBLY_FLAKY":        50,
        "LIKELY_FLAKY":          30,
    }.get(verdict, 50)

    return {"verdict": verdict, "confidence": confidence, "evidence": evidence}


def _compute_confidence(
    failure_type: str,
    lt_rca_present: bool,
    session_metadata_present: bool,
    console_errors: list,
    network_errors: list,
    he_categories: list,
    mcp_details_present: bool,
    error_message: str,
    flakiness_verdict: str,
) -> int:
    base = _BASE_CONFIDENCE.get(failure_type, 5)

    bonuses = 0
    if lt_rca_present:            bonuses += 20
    if console_errors:            bonuses += 15
    if network_errors:            bonuses += 15
    if he_categories:             bonuses += 10
    if mcp_details_present:       bonuses += 10
    if error_message:             bonuses += 10
    if session_metadata_present:  bonuses += 5

    penalties = 0
    if failure_type == "UNKNOWN_FAILURE" and bonuses == 0:
        penalties += 20
    if flakiness_verdict == "LIKELY_FLAKY":
        penalties += 5

    score = base + bonuses - penalties
    if failure_type == "DATA_UNAVAILABLE":
        return min(25, max(0, score))
    return min(100, max(0, score))


def _synthesize_root_cause(
    base_rca: str,
    failure_type: str,
    console_errors: list,
    network_errors: list,
    he_categories: list,
    error_message: str,
    browser_correlation: dict,
    flakiness: dict,
) -> str:
    parts = []

    if base_rca and base_rca not in ("N/A", "API error", "skipped"):
        parts.append(base_rca.rstrip("."))

    if console_errors and failure_type.startswith("PLAYWRIGHT_"):
        parts.append(f"Console error: {console_errors[0][:200]}")

    if network_errors:
        err = network_errors[0]
        parts.append(f"Network failure: {err.get('method','GET')} {err.get('url','')[:120]} → HTTP {err.get('status','?')}")

    if he_categories:
        top = sorted(he_categories, key=lambda c: c.get("count", 0), reverse=True)[0]
        desc = top.get("description", top.get("category", ""))
        if desc:
            parts.append(f"HE category: {desc[:150]}")

    scope = browser_correlation.get("failure_scope", "NO_DATA")
    if scope == "ALL_BROWSERS":
        parts.append("Failure is systemic across all tested browsers")
    elif scope == "SINGLE_BROWSER":
        failed = browser_correlation.get("failed_browsers", [])
        parts.append(f"Isolated to {failed[0] if failed else 'one browser'}")

    flaky_v = flakiness.get("verdict", "REPRODUCIBLE")
    if "FLAKY" in flaky_v:
        parts.append("Behaviour is intermittent — seen on retry")
    else:
        parts.append("Consistent — not flaky")

    if not parts and error_message:
        parts.append(error_message[:300])

    result = ". ".join(p.rstrip(".") for p in parts if p)
    return (result[:600] + "…") if len(result) > 600 else result


def _build_enriched_remediation(
    existing_remediation: dict,
    console_errors: list,
    network_errors: list,
    video_url: str,
    screenshot_url: str,
    flakiness_verdict: str,
) -> dict:
    remediation = dict(existing_remediation)

    remediation["evidence_links"] = {
        "video":      video_url,
        "screenshot": screenshot_url,
    }

    specific = ""
    if console_errors:
        specific = console_errors[0][:200]
    elif network_errors:
        err = network_errors[0]
        specific = f"{err.get('method','GET')} {err.get('url','')[:120]} → HTTP {err.get('status','?')}"
    remediation["specific_error"] = specific

    if "FLAKY" in flakiness_verdict:
        remediation["retry_guidance"] = "Flaky — investigate stability before patching"
    else:
        patch_target = existing_remediation.get("patch_target", "none")
        if patch_target != "none":
            remediation["retry_guidance"] = f"Patch {patch_target} and rerun — reproducible failure"
        else:
            remediation["retry_guidance"] = "Reproducible — investigate application defect"

    return remediation


# ── Per-failure enrichment ───────────────────────────────────────────────────

async def _enrich_single_failure(
    failure: dict,
    rca_lookup: dict,
    normalized_results: list,
    task_lookup: dict,
    client,
    mcp_session,
    semaphore: asyncio.Semaphore,
) -> dict:
    async with semaphore:
        scenario_id  = failure.get("failed_scenario", "")
        requirement_id = failure.get("failed_requirement", "")
        failure_type   = failure.get("failure_type", "UNKNOWN_FAILURE")
        error_message  = failure.get("error_message", "")
        session_links  = failure.get("session_links", [])
        existing_rem   = failure.get("auto_remediation", {})

        # Find primary session
        primary_link = session_links[0] if session_links else ""
        session_id = _extract_test_id(primary_link)
        task_id = task_lookup.get(primary_link, "")

        # Find base RCA
        rca_entry = (
            rca_lookup["by_link"].get(primary_link)
            or rca_lookup["by_test_id"].get(session_id)
            or {}
        )
        base_rca = rca_entry.get("root_cause", "")
        lt_rca_present = bool(base_rca and base_rca not in ("N/A", "", "API error"))

        # Collect all evidence in parallel
        evidence = await _collect_session_evidence(client, session_id, task_id)
        metadata        = evidence["metadata"]
        console_errors  = evidence["console_errors"]
        network_errors  = evidence["network_errors"]
        he_categories   = evidence["he_categories"]

        # MCP details (best-effort)
        mcp_details = await _fetch_mcp_session_details(mcp_session, session_id)
        mcp_enriched = bool(mcp_details)

        # Cross-browser correlation
        browser_correlation = _correlate_browsers(scenario_id, normalized_results)
        retry_info = _detect_retry_runs(scenario_id, normalized_results)
        flakiness = _assess_flakiness(retry_info, browser_correlation, he_categories, console_errors)

        confidence = _compute_confidence(
            failure_type      = failure_type,
            lt_rca_present    = lt_rca_present,
            session_metadata_present = bool(metadata),
            console_errors    = console_errors,
            network_errors    = network_errors,
            he_categories     = he_categories,
            mcp_details_present = mcp_enriched,
            error_message     = error_message,
            flakiness_verdict = flakiness["verdict"],
        )

        confidence_band = "HIGH" if confidence >= 75 else ("MEDIUM" if confidence >= 50 else "LOW")

        enhanced_rca = _synthesize_root_cause(
            base_rca            = base_rca,
            failure_type        = failure_type,
            console_errors      = console_errors,
            network_errors      = network_errors,
            he_categories       = he_categories,
            error_message       = error_message,
            browser_correlation = browser_correlation,
            flakiness           = flakiness,
        )

        video_url      = metadata.get("video_url", "")
        screenshot_url = metadata.get("screenshot_url", "")
        enriched_rem   = _build_enriched_remediation(
            existing_remediation = existing_rem,
            console_errors       = console_errors,
            network_errors       = network_errors,
            video_url            = video_url,
            screenshot_url       = screenshot_url,
            flakiness_verdict    = flakiness["verdict"],
        )

        return {
            "scenario_id":        scenario_id,
            "requirement_id":     requirement_id,
            "failure_type":       failure_type,
            "confidence":         confidence,
            "confidence_band":    confidence_band,
            "primary_session_id": session_id,
            "session_link":       primary_link,
            "base_rca":           base_rca,
            "enhanced_rca":       enhanced_rca,
            "environment": {
                "browser":         metadata.get("browser", rca_entry.get("browser", "")),
                "browser_version": metadata.get("browser_version", ""),
                "os":              metadata.get("os", ""),
                "duration_ms":     metadata.get("duration_ms", 0),
            },
            "artifacts": {
                "video_url":      video_url,
                "screenshot_url": screenshot_url,
            },
            "console_errors":    console_errors,
            "network_errors":    network_errors,
            "he_categories":     he_categories,
            "retry_info":        retry_info,
            "browser_correlation": browser_correlation,
            "flakiness":         flakiness,
            "remediation":       enriched_rem,
            "mcp_enriched":      mcp_enriched,
        }


# ── Summary computation ──────────────────────────────────────────────────────

def _compute_summary(analyses: list) -> dict:
    high = sum(1 for a in analyses if a["confidence_band"] == "HIGH")
    medium = sum(1 for a in analyses if a["confidence_band"] == "MEDIUM")
    low = sum(1 for a in analyses if a["confidence_band"] == "LOW")
    reproducible = sum(1 for a in analyses if "FLAKY" not in a["flakiness"]["verdict"])
    likely_flaky = sum(1 for a in analyses if a["flakiness"]["verdict"] == "LIKELY_FLAKY")
    systemic = sum(1 for a in analyses if a["browser_correlation"].get("is_systemic"))
    isolated = sum(1 for a in analyses if a["browser_correlation"].get("failure_scope") == "SINGLE_BROWSER")
    return {
        "high_confidence":   high,
        "medium_confidence": medium,
        "low_confidence":    low,
        "reproducible":      reproducible,
        "likely_flaky":      likely_flaky,
        "systemic_failures": systemic,
        "isolated_failures": isolated,
    }


# ── Historical storage ───────────────────────────────────────────────────────

def _append_history(payload: dict) -> None:
    history_path = Path("reports/rca_history.json")
    history = _load_json(str(history_path), [])
    if not isinstance(history, list):
        history = []

    run_record = {
        "run_number":        payload.get("run_number", ""),
        "generated_at":      payload.get("generated_at", ""),
        "total_failures":    payload.get("total_failures_analyzed", 0),
        "high_confidence":   payload.get("summary", {}).get("high_confidence", 0),
        "medium_confidence": payload.get("summary", {}).get("medium_confidence", 0),
        "low_confidence":    payload.get("summary", {}).get("low_confidence", 0),
        "systemic_failures": payload.get("summary", {}).get("systemic_failures", 0),
        "likely_flaky":      payload.get("summary", {}).get("likely_flaky", 0),
        "failure_types":     {},
        "scenario_ids":      [],
    }
    for a in payload.get("analyses", []):
        ftype = a.get("failure_type", "UNKNOWN_FAILURE")
        run_record["failure_types"][ftype] = run_record["failure_types"].get(ftype, 0) + 1
        sc = a.get("scenario_id", "")
        if sc and sc not in run_record["scenario_ids"]:
            run_record["scenario_ids"].append(sc)

    # Replace existing record for same run_number, then append
    history = [h for h in history if h.get("run_number") != run_record["run_number"]]
    history.append(run_record)
    if len(history) > 50:
        history = history[-50:]

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    print(f"[rca_enrichment] history updated: {len(history)} run(s)")


# ── Markdown report ──────────────────────────────────────────────────────────

def _write_markdown(payload: dict) -> None:
    lines = [
        "# Enriched RCA Report — Autonomous Failure Diagnosis",
        "",
        f"Generated: {payload['generated_at']}  |  Run: {payload.get('run_number', 'local')}  |  "
        f"MCP: {'available' if payload.get('mcp_available') else 'unavailable'}",
        "",
    ]
    summary = payload.get("summary", {})
    lines += [
        "## Summary",
        "",
        "| Band | Count | Flaky | Systemic |",
        "|---|---|---|---|",
        f"| HIGH (75-100) | {summary.get('high_confidence',0)} | {summary.get('likely_flaky',0)} flaky | {summary.get('systemic_failures',0)} systemic |",
        f"| MEDIUM (50-74) | {summary.get('medium_confidence',0)} | | |",
        f"| LOW (<50) | {summary.get('low_confidence',0)} | | |",
        "",
        "## Analyses",
        "",
    ]
    for a in payload.get("analyses", []):
        bc = a.get("browser_correlation", {})
        fl = a.get("flakiness", {})
        rem = a.get("remediation", {})
        lines += [
            f"### {a['scenario_id']} ({a['requirement_id']}) — {a['failure_type']}",
            "",
            f"- **Confidence:** {a['confidence']}% ({a['confidence_band']})",
            f"- **Scope:** {bc.get('failure_scope','NO_DATA')} (failed: {bc.get('failed_browsers',[])}, passed: {bc.get('passed_browsers',[])})",
            f"- **Flakiness:** {fl.get('verdict','REPRODUCIBLE')} — {'; '.join(fl.get('evidence',[]))}",
            "",
            f"**Enhanced Root Cause:** {a.get('enhanced_rca','N/A')}",
            "",
            f"**Recommended Action:** {rem.get('recommended_action','N/A')}",
        ]
        if rem.get("patch_detail"):
            lines.append(f"**Patch:** {rem['patch_detail']}")
        if rem.get("retry_guidance"):
            lines.append(f"**Next step:** {rem['retry_guidance']}")
        if a.get("console_errors"):
            lines.append(f"**Console errors:** `{a['console_errors'][0][:200]}`")
        if a.get("network_errors"):
            err = a["network_errors"][0]
            lines.append(f"**Network failure:** `{err.get('method','GET')} {err.get('url','')[:120]}` → HTTP {err.get('status','?')}")
        ev = rem.get("evidence_links", {})
        if ev.get("video"):
            lines.append(f"**Video:** {ev['video']}")
        if ev.get("screenshot"):
            lines.append(f"**Screenshot:** {ev['screenshot']}")
        lines.append("")

    Path("reports/rca_enriched.md").write_text("\n".join(lines), encoding="utf-8")
    print("[rca_enrichment] wrote reports/rca_enriched.md")


# ── Main async core ──────────────────────────────────────────────────────────

async def _run_enrichment() -> dict:
    failures, rca_analyses, normalized_results, he_tasks = _load_input_artifacts()

    if not failures:
        print("[rca_enrichment] no failures to enrich — writing empty report")
        return {
            "generated_at":           datetime.now(timezone.utc).isoformat(),
            "run_number":             RUN_NUMBER,
            "schema_version":         SCHEMA_VERSION,
            "total_failures_analyzed": 0,
            "mcp_available":          False,
            "credentials_present":    bool(LT_USERNAME and LT_ACCESS_KEY),
            "summary":                {k: 0 for k in ("high_confidence","medium_confidence","low_confidence","reproducible","likely_flaky","systemic_failures","isolated_failures")},
            "analyses":               [],
        }

    rca_lookup   = _build_rca_lookup(rca_analyses)
    task_lookup  = _build_task_lookup(he_tasks)
    semaphore    = asyncio.Semaphore(MAX_CONCURRENT)
    mcp_available = False

    analyses = []

    if not _HAS_HTTPX:
        print("[rca_enrichment] httpx not installed — degrading to empty evidence")
        for f in failures:
            analyses.append(await _enrich_single_failure(
                f, rca_lookup, normalized_results, task_lookup,
                None, None, semaphore,
            ))
    else:
        async with httpx.AsyncClient() as client:
            mcp_session = None

            if _HAS_MCP and LT_USERNAME and LT_ACCESS_KEY:
                try:
                    mcp_url_auth = f"{MCP_URL}?username={LT_USERNAME}&accessKey={LT_ACCESS_KEY}"
                    headers = {"x-lt-username": LT_USERNAME, "x-lt-access-key": LT_ACCESS_KEY}
                    # Open MCP connection for the duration of enrichment
                    # We do a quick list_tools to verify connection, then pass session to enrichers
                    # Use a shared reference via a wrapper approach
                    async with sse_client(mcp_url_auth, headers=headers) as (read, write):
                        async with ClientSession(read, write) as sess:
                            await sess.initialize()
                            mcp_available = True
                            print("[rca_enrichment] MCP connected")
                            tasks = [
                                _enrich_single_failure(
                                    f, rca_lookup, normalized_results, task_lookup,
                                    client, sess, semaphore,
                                )
                                for f in failures
                            ]
                            analyses = list(await asyncio.gather(*tasks))
                except Exception as exc:
                    print(f"[rca_enrichment] MCP unavailable: {exc} — REST-only mode")

            if not analyses:
                tasks = [
                    _enrich_single_failure(
                        f, rca_lookup, normalized_results, task_lookup,
                        client, None, semaphore,
                    )
                    for f in failures
                ]
                analyses = list(await asyncio.gather(*tasks))

    summary = _compute_summary(analyses)
    print(f"[rca_enrichment] enriched {len(analyses)} failure(s): "
          f"HIGH={summary['high_confidence']} MEDIUM={summary['medium_confidence']} LOW={summary['low_confidence']}")

    return {
        "generated_at":            datetime.now(timezone.utc).isoformat(),
        "run_number":              RUN_NUMBER,
        "schema_version":          SCHEMA_VERSION,
        "total_failures_analyzed": len(analyses),
        "mcp_available":           mcp_available,
        "credentials_present":     bool(LT_USERNAME and LT_ACCESS_KEY),
        "summary":                 summary,
        "analyses":                analyses,
    }


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    if not LT_USERNAME or not LT_ACCESS_KEY:
        print("[rca_enrichment] LT credentials absent — writing minimal report")
        payload = {
            "generated_at":            datetime.now(timezone.utc).isoformat(),
            "run_number":              RUN_NUMBER,
            "schema_version":          SCHEMA_VERSION,
            "total_failures_analyzed": 0,
            "mcp_available":           False,
            "credentials_present":     False,
            "summary":                 {k: 0 for k in ("high_confidence","medium_confidence","low_confidence","reproducible","likely_flaky","systemic_failures","isolated_failures")},
            "analyses":                [],
        }
        Path("reports/rca_enriched.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return 0

    try:
        payload = asyncio.run(_run_enrichment())
    except Exception as exc:
        print(f"[rca_enrichment] unhandled error: {exc} — writing empty report")
        payload = {
            "generated_at":            datetime.now(timezone.utc).isoformat(),
            "run_number":              RUN_NUMBER,
            "schema_version":          SCHEMA_VERSION,
            "total_failures_analyzed": 0,
            "mcp_available":           False,
            "credentials_present":     True,
            "error":                   str(exc),
            "summary":                 {k: 0 for k in ("high_confidence","medium_confidence","low_confidence","reproducible","likely_flaky","systemic_failures","isolated_failures")},
            "analyses":                [],
        }

    Path("reports").mkdir(exist_ok=True)
    Path("reports/rca_enriched.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("[rca_enrichment] wrote reports/rca_enriched.json")

    if payload.get("analyses"):
        _write_markdown(payload)
        _append_history(payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
