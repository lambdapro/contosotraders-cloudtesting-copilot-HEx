"""
RCA parser for the GHA rca-summary job.

Enriches Kane NDJSON + Playwright JUnit XML results using the LambdaTest MCP
server (https://mcp.lambdatest.com/mcp) for session recordings, HyperExecute
job data, and failure logs.  Falls back to local artifact parsing when MCP
is unavailable or credentials are missing.

Usage:
    python rca_parser.py \
        --kane artifacts/kane-results/ \
        --playwright artifacts/playwright-reports/ \
        --output artifacts/rca_result.json \
        --summary $GITHUB_STEP_SUMMARY \
        --he-job-id <hyperexecute-job-id>

Environment variables required for MCP enrichment:
    LT_USERNAME     — LambdaTest username
    LT_ACCESS_KEY   — LambdaTest access key
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# MCP config
# ---------------------------------------------------------------------------

_MCP_URL = "https://mcp.lambdatest.com/mcp"
_JSON_BLOCK_RE = re.compile(r"```json\s*([\s\S]*?)\s*```")


def _parse_mcp_text(text: str) -> dict:
    m = _JSON_BLOCK_RE.search(text)
    candidate = m.group(1) if m else text.strip()
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

_CLASSIFICATIONS = {
    "APPLICATION_DEFECT": [
        "not found", "not visible", "no element", "does not exist",
        "element not found", "selector", "locator", "text not found",
        "AssertionError", "assertion",
    ],
    "TIMING": ["timeout", "timed out", "exceeded", "wait"],
    "NAVIGATION": ["navigation", "net::ERR", "failed to load", "ERR_CONNECTION"],
    "AUTH": ["login", "401", "403", "unauthorized", "credential"],
    "INFRA": ["browser", "chromium", "playwright", "cdp", "WebSocket"],
}


def _classify_failure(reason: str) -> str:
    reason_lower = reason.lower()
    for label, keywords in _CLASSIFICATIONS.items():
        if any(kw.lower() in reason_lower for kw in keywords):
            return label
    return "UNKNOWN_FAILURE"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class KaneResult:
    test_name: str
    status: str              # "passed" | "failed" | "error" | "timeout"
    one_liner: str
    reason: str
    duration: float
    share_link: str
    test_case_link: str
    code_export_path: str
    failure_class: str = ""


@dataclass
class PlaywrightResult:
    test_name: str
    classname: str
    browser: str
    status: str              # "passed" | "failed" | "skipped"
    failure_message: str
    failure_class: str = ""
    session_link: str = ""   # enriched by LambdaTest MCP


@dataclass
class RcaResult:
    kane_results: list[KaneResult] = field(default_factory=list)
    playwright_results: list[PlaywrightResult] = field(default_factory=list)
    kane_pass_count: int = 0
    kane_fail_count: int = 0
    playwright_pass_count: int = 0
    playwright_fail_count: int = 0
    overall_verdict: str = "UNKNOWN"  # "GREEN" | "YELLOW" | "RED"
    fix_suggestions: list[dict] = field(default_factory=list)
    summary_lines: list[str] = field(default_factory=list)
    he_job_link: str = ""    # enriched by LambdaTest MCP
    he_job_id: str = ""


# ---------------------------------------------------------------------------
# Kane NDJSON parser
# ---------------------------------------------------------------------------

def _parse_kane_ndjson(ndjson_path: Path) -> KaneResult | None:
    test_name = ndjson_path.stem
    run_end: dict = {}
    share_link = ""
    test_case_link = ""
    code_export_path = ""

    try:
        lines = ndjson_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None

    for line in lines:
        line = line.strip()
        if not line or line.startswith("EXIT_CODE:"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if "ShareLink:" in line or "share.testmuai" in line:
                m = re.search(r"https?://\S+", line)
                if m:
                    share_link = m.group(0)
            if "TestCase:" in line:
                m = re.search(r"https?://\S+", line)
                if m:
                    test_case_link = m.group(0)
            if "CodeExport:" in line or "code-export" in line:
                m = re.search(r"file://\S+|/[\w./\-]+code.export\S*", line)
                if m:
                    code_export_path = m.group(0)
            continue

        etype = event.get("type") or event.get("event", "")
        if etype in ("run_end", "runEnd"):
            run_end = event

    if not run_end:
        exit_codes = [l for l in lines if l.startswith("EXIT_CODE:")]
        exit_code = int(exit_codes[-1].split(":")[1]) if exit_codes else 1
        status = "passed" if exit_code == 0 else "failed"
        return KaneResult(
            test_name=test_name, status=status, one_liner="",
            reason="No run_end event found", duration=0,
            share_link=share_link, test_case_link=test_case_link,
            code_export_path=code_export_path,
            failure_class=_classify_failure("No run_end event") if status != "passed" else "",
        )

    status = run_end.get("status", "unknown")
    if status == "pass":
        status = "passed"
    elif status == "fail":
        status = "failed"

    reason = run_end.get("reason") or run_end.get("summary") or ""
    one_liner = run_end.get("one_liner") or run_end.get("oneLiner") or ""
    duration = float(run_end.get("duration") or run_end.get("duration_ms", 0) or 0)

    final_state = run_end.get("final_state") or {}
    share_link = share_link or run_end.get("share_link") or final_state.get("share_link") or ""
    test_case_link = test_case_link or run_end.get("test_case_link") or ""

    failure_class = _classify_failure(reason) if status != "passed" else ""

    return KaneResult(
        test_name=test_name, status=status,
        one_liner=one_liner, reason=reason, duration=duration,
        share_link=share_link, test_case_link=test_case_link,
        code_export_path=code_export_path, failure_class=failure_class,
    )


def parse_kane_dir(kane_dir: str) -> list[KaneResult]:
    results: list[KaneResult] = []
    for f in Path(kane_dir).glob("*.ndjson"):
        r = _parse_kane_ndjson(f)
        if r:
            results.append(r)
    return results


# ---------------------------------------------------------------------------
# Playwright JUnit XML parser
# ---------------------------------------------------------------------------

def _browser_from_classname(classname: str) -> str:
    cl = classname.lower()
    if "edge" in cl or "microsoftedge" in cl:
        return "edge"
    if "firefox" in cl:
        return "firefox"
    if "chrome" in cl or "chromium" in cl:
        return "chrome"
    return "unknown"


def parse_playwright_reports(reports_dir: str) -> list[PlaywrightResult]:
    results: list[PlaywrightResult] = []
    reports_path = Path(reports_dir)

    for junit_file in (
        list(reports_path.rglob("junit*.xml"))
        + list(reports_path.rglob("results*.xml"))
    ):
        try:
            tree = ET.parse(junit_file)
            root = tree.getroot()
            for tc in root.findall(".//testcase"):
                name = tc.get("name", "unknown")
                classname = tc.get("classname", "")
                browser = _browser_from_classname(classname)
                failure = tc.find("failure")
                error = tc.find("error")
                skipped = tc.find("skipped")

                if skipped is not None:
                    status = "skipped"
                    msg = ""
                elif failure is not None:
                    status = "failed"
                    msg = failure.get("message", "") or (failure.text or "")[:500]
                elif error is not None:
                    status = "failed"
                    msg = error.get("message", "") or (error.text or "")[:500]
                else:
                    status = "passed"
                    msg = ""

                fc = _classify_failure(msg) if status == "failed" else ""
                results.append(PlaywrightResult(
                    test_name=name, classname=classname, browser=browser,
                    status=status, failure_message=msg, failure_class=fc,
                ))
        except Exception as e:
            print(f"[rca_parser] warning: failed to parse {junit_file}: {e}", file=sys.stderr)

    return results


# ---------------------------------------------------------------------------
# LambdaTest MCP enrichment
# ---------------------------------------------------------------------------

async def _enrich_via_mcp(he_job_id: str, rca: RcaResult) -> None:
    """
    Connect to the LambdaTest MCP server and enrich RCA with HyperExecute job
    data — session recording links, task-level status, and job dashboard URL.

    Silently skips (with a warning) on any connection or parsing failure.
    """
    lt_user = os.environ.get("LT_USERNAME", "")
    lt_key = os.environ.get("LT_ACCESS_KEY", "")

    if not lt_user or not lt_key:
        print("[rca_mcp] skipped — LT_USERNAME / LT_ACCESS_KEY not set")
        return
    if not he_job_id:
        print("[rca_mcp] skipped — no HyperExecute job ID provided")
        return

    try:
        from mcp import ClientSession  # noqa: PLC0415
        from mcp.client.sse import sse_client  # noqa: PLC0415
    except ImportError:
        print("[rca_mcp] skipped — mcp package not installed (pip install mcp)")
        return

    mcp_url = f"{_MCP_URL}?username={lt_user}&accessKey={lt_key}"
    headers = {"x-lt-username": lt_user, "x-lt-access-key": lt_key}

    try:
        async with sse_client(mcp_url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Fetch HyperExecute job info
                raw = await session.call_tool("getHyperExecuteJobInfo", {"jobId": he_job_id})
                text = raw.content[0].text if raw.content else "{}"
                job_data = _parse_mcp_text(text)

                job_info = (
                    job_data.get("jobInfo")
                    or job_data.get("data")
                    or job_data
                )
                rca.he_job_link = (
                    job_info.get("jobLink")
                    or f"https://hyperexecute.lambdatest.com/hyperexecute/task?jobId={he_job_id}"
                )
                print(f"[rca_mcp] HE job: {rca.he_job_link}")

                # Extract per-task session links and match to Playwright results
                tasks = (
                    job_data.get("tasks")
                    or job_info.get("tasks")
                    or job_data.get("taskDetails")
                    or []
                )
                enriched = 0
                for task in tasks:
                    test_id = task.get("testID") or task.get("sessionID") or task.get("test_id", "")
                    task_name = (task.get("name") or task.get("scenario_name") or "").lower()
                    task_status = task.get("status", "")
                    session_link = (
                        f"https://automation.lambdatest.com/test?testID={test_id}"
                        if test_id else ""
                    )

                    for pw in rca.playwright_results:
                        if not pw.session_link and task_name and pw.test_name:
                            pw_lower = pw.test_name.lower()
                            if task_name in pw_lower or pw_lower in task_name:
                                pw.session_link = session_link
                                if task_status and not pw.failure_class:
                                    # Reconcile status from MCP if local XML was ambiguous
                                    if task_status in ("failed", "fail", "error"):
                                        pw.status = "failed"
                                    elif task_status in ("passed", "pass", "completed"):
                                        pw.status = "passed"
                                enriched += 1
                                break

                print(f"[rca_mcp] enriched {enriched}/{len(rca.playwright_results)} playwright results")

                # Try to fetch Kane session details via MCP (TMS session links)
                for kr in rca.kane_results:
                    if not kr.share_link and not kr.test_case_link:
                        try:
                            raw2 = await session.call_tool(
                                "getKaneSessionDetails", {"testName": kr.test_name}
                            )
                            text2 = raw2.content[0].text if raw2.content else "{}"
                            kane_data = _parse_mcp_text(text2)
                            kr.share_link = kr.share_link or kane_data.get("shareLink", "")
                            kr.test_case_link = (
                                kr.test_case_link or kane_data.get("testCaseLink", "")
                            )
                        except Exception:
                            pass

    except Exception as exc:
        print(f"[rca_mcp] warning: MCP enrichment failed: {exc!r} — using local data only")
        if not rca.he_job_link and he_job_id:
            rca.he_job_link = (
                f"https://hyperexecute.lambdatest.com/hyperexecute/task?jobId={he_job_id}"
            )


# ---------------------------------------------------------------------------
# Fix suggestions
# ---------------------------------------------------------------------------

_FIX_TEMPLATES = {
    "APPLICATION_DEFECT": {
        "banner": (
            "Add a banner element to the header component.\n"
            "In header.js render(), above <Categories>:\n\n"
            "  <div className=\"memorial-day-banner\" data-testid=\"promo-banner\">\n"
            "    Memorial Day Sale — Up to 30% Off! Shop Now\n"
            "  </div>\n\n"
            "Alternative: Wire existing headerMessage.js (props: type, icon, message) "
            "into header.js and pass the banner text as the `message` prop."
        ),
    },
}


def _generate_fix(failure_class: str, test_name: str) -> dict | None:
    if failure_class == "APPLICATION_DEFECT":
        name_lower = test_name.lower()
        for keyword, template in _FIX_TEMPLATES["APPLICATION_DEFECT"].items():
            if keyword in name_lower:
                return {"type": "code_change", "component": "header", "suggestion": template}
        return {
            "type": "code_change",
            "component": "unknown",
            "suggestion": (
                f"The feature tested by '{test_name}' is not yet implemented. "
                "Add the missing UI element or functionality."
            ),
        }
    if failure_class == "TIMING":
        return {
            "type": "test_fix",
            "component": "selector",
            "suggestion": "Increase wait timeout or add explicit wait-for-element calls.",
        }
    return None


# ---------------------------------------------------------------------------
# Verdict + markdown summary
# ---------------------------------------------------------------------------

def _compute_verdict(rca: RcaResult) -> str:
    total = (
        rca.kane_pass_count + rca.kane_fail_count
        + rca.playwright_pass_count + rca.playwright_fail_count
    )
    if total == 0:
        return "UNKNOWN"
    passed = rca.kane_pass_count + rca.playwright_pass_count
    rate = passed / total * 100
    if rate >= 90 and rca.kane_fail_count == 0 and rca.playwright_fail_count == 0:
        return "GREEN"
    if rate >= 75:
        return "YELLOW"
    return "RED"


def _build_markdown(rca: RcaResult) -> str:
    verdict_emoji = {
        "GREEN": "✅", "YELLOW": "⚠️", "RED": "❌", "UNKNOWN": "❓"
    }.get(rca.overall_verdict, "❓")

    lines = [
        f"## Agentic STLC — Feature Validation Report {verdict_emoji}",
        "",
        f"**Verdict: {rca.overall_verdict}**",
    ]

    if rca.he_job_link:
        lines += ["", f"**HyperExecute Job:** [{rca.he_job_id or 'View'}]({rca.he_job_link})"]

    lines += [
        "",
        "### Kane AI Functional Verification",
        f"- Passed: {rca.kane_pass_count}  Failed: {rca.kane_fail_count}",
        "",
        "| Test | Status | Details |",
        "|------|--------|---------|",
    ]
    for r in rca.kane_results:
        status_icon = "✅" if r.status == "passed" else "❌"
        detail = r.one_liner or r.reason[:80]
        links = []
        if r.share_link:
            links.append(f"[Session]({r.share_link})")
        if r.test_case_link:
            links.append(f"[TMS]({r.test_case_link})")
        if r.code_export_path:
            links.append(f"[Code Export]({r.code_export_path})")
        link_str = " · ".join(links) if links else ""
        lines.append(
            f"| `{r.test_name}` | {status_icon} {r.status} | {detail}{' — ' + link_str if link_str else ''} |"
        )

    lines += [
        "",
        "### HyperExecute Playwright Regression (Chrome · Firefox · Edge)",
        f"- Passed: {rca.playwright_pass_count}  Failed: {rca.playwright_fail_count}",
        "",
        "| Browser | Test | Status | Failure | Session |",
        "|---------|------|--------|---------|---------|",
    ]
    for r in rca.playwright_results:
        status_icon = (
            "✅" if r.status == "passed" else ("⚠️" if r.status == "skipped" else "❌")
        )
        msg = r.failure_message[:100].replace("\n", " ") if r.failure_message else ""
        session_col = f"[View]({r.session_link})" if r.session_link else ""
        lines.append(
            f"| {r.browser} | `{r.test_name}` | {status_icon} {r.status} | {msg} | {session_col} |"
        )

    if rca.fix_suggestions:
        lines += ["", "### Fix Suggestions", ""]
        for fix in rca.fix_suggestions:
            lines.append(
                f"**Type:** `{fix.get('type', '')}` — **Component:** `{fix.get('component', '')}`"
            )
            lines += ["", "```", fix.get("suggestion", ""), "```", ""]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    kane_dir: str,
    playwright_dir: str,
    output_path: str,
    summary_path: str,
    he_job_id: str = "",
) -> RcaResult:
    rca = RcaResult(he_job_id=he_job_id)

    if Path(kane_dir).exists():
        rca.kane_results = parse_kane_dir(kane_dir)
        rca.kane_pass_count = sum(1 for r in rca.kane_results if r.status == "passed")
        rca.kane_fail_count = sum(1 for r in rca.kane_results if r.status != "passed")

    if Path(playwright_dir).exists():
        rca.playwright_results = parse_playwright_reports(playwright_dir)
        rca.playwright_pass_count = sum(1 for r in rca.playwright_results if r.status == "passed")
        rca.playwright_fail_count = sum(1 for r in rca.playwright_results if r.status == "failed")

    # Enrich with LambdaTest MCP data (session recordings, HE job link)
    if he_job_id or (os.environ.get("LT_USERNAME") and os.environ.get("LT_ACCESS_KEY")):
        asyncio.run(_enrich_via_mcp(he_job_id, rca))
    else:
        # Construct HE job link from env fallback
        if he_job_id:
            rca.he_job_link = (
                f"https://hyperexecute.lambdatest.com/hyperexecute/task?jobId={he_job_id}"
            )

    # Fix suggestions from failures
    seen_fixes: set[str] = set()
    for r in rca.kane_results:
        if r.failure_class and r.failure_class not in seen_fixes:
            fix = _generate_fix(r.failure_class, r.test_name)
            if fix:
                rca.fix_suggestions.append(fix)
                seen_fixes.add(r.failure_class)
    for r in rca.playwright_results:
        if r.failure_class and r.failure_class not in seen_fixes:
            fix = _generate_fix(r.failure_class, r.test_name)
            if fix:
                rca.fix_suggestions.append(fix)
                seen_fixes.add(r.failure_class)

    rca.overall_verdict = _compute_verdict(rca)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(rca), indent=2), encoding="utf-8")
    print(f"[rca_parser] wrote {output_path}")

    md = _build_markdown(rca)
    if summary_path:
        try:
            Path(summary_path).write_text(md, encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"[rca_parser] warning: could not write summary: {e}", file=sys.stderr)
    else:
        print(md)

    print(
        f"[rca_parser] verdict={rca.overall_verdict} "
        f"kane={rca.kane_pass_count}✓/{rca.kane_fail_count}✗ "
        f"playwright={rca.playwright_pass_count}✓/{rca.playwright_fail_count}✗"
    )

    return rca


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RCA parser — Kane + Playwright + LambdaTest MCP")
    parser.add_argument("--kane", default="artifacts/kane-results/", help="Kane NDJSON directory")
    parser.add_argument("--playwright", default="artifacts/playwright-reports/",
                        help="Playwright report directory")
    parser.add_argument("--output", default="artifacts/rca_result.json", help="Output JSON path")
    parser.add_argument("--summary", default="", help="GitHub step summary path ($GITHUB_STEP_SUMMARY)")
    parser.add_argument("--he-job-id", default="", help="HyperExecute job ID for MCP enrichment")
    args = parser.parse_args()

    result = run(args.kane, args.playwright, args.output, args.summary, args.he_job_id)
    sys.exit(0 if result.overall_verdict == "GREEN" else 1)
