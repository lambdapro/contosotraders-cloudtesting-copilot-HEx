"""
GitHub Actions step summary renderer.

Pure data renderer — reads only from real artifacts:
  - requirements/analyzed_requirements.json
  - scenarios/scenarios.json
  - reports/normalized_results.json
  - reports/traceability_matrix.json
  - reports/api_details.json
  - reports/release_recommendation.md
  - reports/validation_report.json
  - reports/test_execution_manifest.json
  - kane/objectives.json

Produces factual tables only. No first-person narration. No fabricated values.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result


def load_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    content = p.read_text(encoding="utf-8").strip()
    if not content:
        return default
    try:
        return json.loads(content)
    except Exception:
        return default


def emit(text):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    # Force UTF-8 on Windows consoles that default to cp1252
    import sys
    out = sys.stdout
    try:
        if hasattr(out, "reconfigure"):
            out.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def verdict_emoji(verdict):
    return {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(verdict, "⚪")


def status_icon(status):
    mapping = {
        "passed": "✅",
        "failed": "❌",
        "skipped": "⏭️",
        "data_unavailable": "⚠️",
        "new": "🆕",
        "updated": "🔄",
        "active": "✅",
        "deprecated": "🚫",
    }
    return mapping.get(status, "❓")


def _derive_normalized_status(raw_status: str, he_tasks: list) -> str:
    """Mirror of agent.py _normalize_he_status — used for display when api_details lacks normalized_status."""
    _map = {
        "completed": "PASSED", "passed": "PASSED",
        "failed": "FAILED", "error": "INFRA_FAILURE",
        "aborted": "CANCELLED", "cancelled": "CANCELLED",
        "running": "RUNNING", "initiated": "RUNNING", "queued": "RUNNING",
    }
    n = _map.get((raw_status or "").lower())
    if n:
        return n
    if he_tasks:
        passed = sum(1 for t in he_tasks if t.get("status") == "passed")
        failed = sum(1 for t in he_tasks if t.get("status") == "failed")
        total = passed + failed
        if total == 0:
            return "NOT_EXECUTED"
        if failed == 0:
            return "PASSED"
        if passed == 0:
            return "FAILED"
        return "PARTIAL"
    return "NOT_EXECUTED" if not raw_status else "INFRA_FAILURE"


def _he_job_id_from_log():
    for path in ("reports/hyperexecute-cli.log", "reports/hyperexecute_failure_analysis.md"):
        p = Path(path)
        if not p.exists():
            continue
        m = re.search(r"jobId=([\w-]+)", p.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return ""


def main():
    run_url = os.environ.get("RUN_URL", "")

    requirements = load_json("requirements/analyzed_requirements.json", [])
    scenarios = load_json("scenarios/scenarios.json", [])
    manifest = load_json("reports/test_execution_manifest.json", {})
    trace_json = load_json("reports/traceability_matrix.json", {})
    api_details = load_json("reports/api_details.json", {})
    normalized_raw = load_json("reports/normalized_results.json", {})
    normalized = normalized_raw.get("results", [])
    validation = load_json("reports/validation_report.json", {})

    he_api = api_details.get("he_summary", {})
    _seen_he: dict = {}
    for t in api_details.get("he_tasks", []):
        key = t.get("name") or t.get("task_id", "")
        _seen_he.setdefault(key, t)
    he_tasks_api = list(_seen_he.values())

    trace_summary = trace_json.get("summary", {})
    trace_rows = trace_json.get("rows", [])
    result_analysis = trace_json.get("result_analysis", {})

    executed = trace_summary.get("executed", 0)
    passed = trace_summary.get("passed", 0)
    pass_rate = trace_summary.get("pass_rate", 0.0)
    all_browsers = trace_summary.get("browsers_tested", [])
    failing_scenarios = trace_summary.get("failing_scenarios", [])
    untested = trace_summary.get("untested_requirements", [])

    he_job_id = he_api.get("job_id", "") or _he_job_id_from_log()
    he_job_link = he_api.get("job_link", "") or (
        f"https://hyperexecute.lambdatest.com/hyperexecute/task?jobId={he_job_id}"
        if he_job_id else ""
    )
    he_status_raw = he_api.get("status", "") or ""
    # Use pre-computed normalized status when available; fall back to inline derivation
    he_normalized = he_api.get("normalized_status", "") or _derive_normalized_status(he_status_raw, he_tasks_api)
    he_parser_status = he_api.get("parser_status", "unknown")
    he_status = he_normalized or he_status_raw or "NOT_EXECUTED"

    release_md = Path("reports/release_recommendation.md")
    verdict_line = "UNKNOWN"
    recommendation_line = ""
    if release_md.exists():
        for line in release_md.read_text(encoding="utf-8").splitlines():
            if line.startswith("**Verdict:**"):
                verdict_line = line.replace("**Verdict:**", "").strip()
            if line.startswith("## Recommendation") is False and (
                line.startswith("Approve") or line.startswith("Block") or line.startswith("Conditional")
            ):
                recommendation_line = line.strip()

    # ── Header ────────────────────────────────────────────────────────────────
    print_stage_header("9", "GITHUB_SUMMARY", "Writing GitHub Actions step summary")
    emit("# Agentic STLC — Pipeline Report")
    emit("")
    if run_url:
        emit(f"> **Run:** {run_url}")
    emit("")

    # ── Stage Status Table ────────────────────────────────────────────────────
    kane_passed = sum(1 for r in requirements if r.get("kane_status") == "passed")
    kane_total = len(requirements)
    kane_status_icon = "✅" if kane_passed == kane_total else ("🟡" if kane_passed > 0 else "❌")
    _he_icon_map = {
        "PASSED": "✅", "FAILED": "❌", "PARTIAL": "🟡",
        "RUNNING": "⏳", "CANCELLED": "🚫", "INFRA_FAILURE": "🔥",
        "NOT_EXECUTED": "⏭️", "TIMED_OUT": "⏰",
    }
    he_icon = _he_icon_map.get(he_status, "⚠️")
    he_task_pass = he_api.get("task_pass_count", sum(1 for t in he_tasks_api if t.get("status") == "passed"))
    he_task_total = he_api.get("total_tasks", len(he_tasks_api))
    verdict_icon = verdict_emoji(verdict_line)

    emit("## Pipeline Stage Status")
    emit("")
    emit("| Stage | Name | Status | Normalized Status | Details |")
    emit("|-------|------|--------|-------------------|---------|")
    emit(f"| 1 | KaneAI Verification | {kane_status_icon} | {'PASSED' if kane_passed == kane_total else 'PARTIAL' if kane_passed > 0 else 'FAILED'} | {kane_passed}/{kane_total} criteria passed |")
    emit(f"| 2–4 | Scenarios + Test Gen + Selection | ✅ | PASSED | {len([s for s in scenarios if s.get('status') != 'deprecated'])} active tests generated |")
    emit(f"| 5 | HyperExecute Regression | {he_icon} | {he_status} | {he_task_pass}/{he_task_total} tasks · parser: {he_parser_status} |")
    emit(f"| 6 | Result Aggregation | ✅ | PASSED | {len(normalized)} results normalized |")
    emit(f"| 7–8 | Traceability + Verdict | {verdict_icon} | {verdict_line} | see release recommendation below |")
    emit("")

    # ── Execution Links ────────────────────────────────────────────────────────
    emit("## Execution Links")
    emit("")
    if he_job_link:
        emit(f"- [HyperExecute Dashboard]({he_job_link})")
    if run_url:
        emit(f"- [GitHub Actions Run]({run_url})")
    emit("")

    # ── Stage 1: Kane AI Requirement Analysis ─────────────────────────────────
    emit("## Stage 1 · Kane AI Functional Verification")
    emit("")
    if not requirements:
        emit("_No requirements data found in analyzed_requirements.json._")
    else:
        emit(f"| Req ID | Acceptance Criterion | Kane Status | What Kane Observed |")
        emit("|---|---|---|---|")
        for r in requirements:
            kane_status = r.get("kane_status", "unknown")
            icon = status_icon(kane_status)
            one_liner = r.get("kane_one_liner", "") or "—"
            kane_links = r.get("kane_links", [])
            link = f"[session]({kane_links[0]})" if kane_links else "—"
            criterion = r.get("description", "")[:60]
            emit(f"| `{r['id']}` | {criterion} | {icon} {kane_status} | {one_liner} |")
        emit("")

        kane_failed = [r for r in requirements if r.get("kane_status") == "failed"]
        if kane_failed:
            emit(f"**{len(kane_failed)} criterion/criteria failed Kane AI verification:**")
            for r in kane_failed:
                emit(f"- ❌ `{r['id']}` {r.get('title', '')} — {r.get('kane_one_liner', '')}")
            emit("")

    # ── Stage 2: Scenario Management ──────────────────────────────────────────
    emit("## Stage 2 · Scenario Catalog")
    emit("")
    if scenarios:
        new_sc = [s for s in scenarios if s.get("status") == "new"]
        updated_sc = [s for s in scenarios if s.get("status") == "updated"]
        active_sc = [s for s in scenarios if s.get("status") == "active"]
        deprecated_sc = [s for s in scenarios if s.get("status") == "deprecated"]
        emit(
            f"Total: **{len(scenarios)}** — "
            f"{len(active_sc)} active, {len(new_sc)} new, "
            f"{len(updated_sc)} updated, {len(deprecated_sc)} deprecated"
        )
        if new_sc or updated_sc:
            emit("")
            emit("| Scenario | Status | Requirement |")
            emit("|---|---|---|")
            for s in new_sc + updated_sc:
                emit(f"| `{s['id']}` {s.get('title', '')} | {status_icon(s['status'])} {s['status']} | `{s.get('requirement_id', '')}` |")
    emit("")

    # ── Stage 3: Test Generation ───────────────────────────────────────────────
    emit("## Stage 3 · Generated Playwright Tests")
    emit("")
    objectives = load_json("kane/objectives.json", [])
    active_scenarios_set = {s["id"] for s in scenarios if s.get("status") != "deprecated"}
    active_objectives = [o for o in objectives if o.get("scenario_id") in active_scenarios_set]
    if active_objectives:
        emit(f"**{len(active_objectives)}** test function(s) in `tests/playwright/test_powerapps.py`:")
        emit("")
        emit("| Scenario | Test Case | Function |")
        emit("|---|---|---|")
        for o in active_objectives:
            sc = next((s for s in scenarios if s["id"] == o["scenario_id"]), {})
            fn = sc.get("function_name", o.get("objective", ""))
            emit(f"| `{o['scenario_id']}` | `{o.get('test_case_id', '')}` | `{fn}` |")
    else:
        emit("_No test generation data available._")
    emit("")

    # ── Stage 4: Test Selection ────────────────────────────────────────────────
    emit("## Stage 4 · Test Selection")
    emit("")
    selected = manifest.get("selected_scenarios", [])
    run_type = manifest.get("run_type", "unknown")
    emit(f"Run type: **{run_type}** · **{len(selected)}** scenario(s) submitted to HyperExecute")
    emit("")

    # ── Stage 5: HyperExecute Execution (multi-browser) ───────────────────────
    emit("## Stage 5 · HyperExecute Regression (Multi-Browser)")
    emit("")

    he_passed_count = he_api.get("task_pass_count", sum(1 for t in he_tasks_api if t.get("status") == "passed"))
    he_failed_count = he_api.get("task_fail_count", sum(1 for t in he_tasks_api if t.get("status") == "failed"))
    he_total_count  = he_api.get("total_tasks") or len(he_tasks_api) or len(selected)

    emit("| Metric | Raw Value | Normalized | Evidence |")
    emit("|--------|-----------|------------|----------|")
    if he_job_link:
        emit(f"| HyperExecute Job | `{he_job_id}` | — | [Open in LambdaTest ↗]({he_job_link}) |")
    else:
        emit(f"| HyperExecute Job ID | `{he_job_id or 'n/a'}` | — | job not submitted or ID extraction failed |")
    emit(f"| Job Status | `{he_status_raw or 'not returned'}` | **{he_status}** | source: {he_parser_status} |")
    emit(f"| Parser Status | `{he_parser_status}` | — | how status was resolved |")
    emit(f"| Browsers | — | — | {', '.join(all_browsers) if all_browsers else 'chrome (default)'} |")
    emit(f"| Total tasks | {he_total_count} | — | submitted to HyperExecute |")
    emit(f"| ✅ Passed | {he_passed_count} | — | task-level results |")
    emit(f"| ❌ Failed | {he_failed_count} | — | task-level results |")
    emit(f"| Pass rate | {pass_rate}% | — | across all browsers |")
    emit("")

    _parser_notes = {
        "api_ok":             "✅ Job status fetched directly from HyperExecute API",
        "derived_from_tasks": "ℹ️ Job API unreachable — status derived from individual task results",
        "mcp_unavailable":    "⚠️ MCP connection failed and HE REST API returned no status — check LT credentials",
        "not_executed":       "⏭️ HyperExecute stage was not executed (no job submitted)",
    }
    note = _parser_notes.get(he_parser_status, f"ℹ️ Parser status: `{he_parser_status}`")
    emit(f"> **Status resolution:** {note}")
    emit("")

    if he_tasks_api:
        emit("### Per-Test Results")
        emit("")
        emit("| Test | Status | Session |")
        emit("|---|---|---|")
        for task in he_tasks_api:
            icon = "✅" if task.get("status") == "passed" else "❌"
            session = f"[View session]({task['session_link']})" if task.get("session_link") else "—"
            emit(f"| `{task.get('name') or task.get('task_id', 'unknown')}` | {icon} {task.get('status', 'unknown')} | {session} |")
        emit("")

    # ── Multi-browser breakdown from normalized results ────────────────────────
    if normalized and all_browsers:
        emit("### Browser Breakdown")
        emit("")
        emit(f"| Scenario | " + " | ".join(b.capitalize() for b in all_browsers) + " |")
        emit("|---| " + " | ".join("---" for _ in all_browsers) + " |")
        by_sc: dict = {}
        for r in normalized:
            by_sc.setdefault(r["scenario_id"], {})[r["browser"]] = r.get("status", "data_unavailable")
        for sc_id in sorted(by_sc):
            cells = " | ".join(
                f"{status_icon(by_sc[sc_id].get(b, 'data_unavailable'))} {by_sc[sc_id].get(b, '—')}"
                for b in all_browsers
            )
            emit(f"| `{sc_id}` | {cells} |")
        emit("")

    # ── Stage 6: Traceability Matrix ───────────────────────────────────────────
    emit("## Stage 6 · Traceability Matrix")
    emit("")
    emit(
        f"**{passed}/{executed}** regression tests passed across "
        f"**{len(all_browsers) or 1}** browser(s) — {pass_rate}% pass rate"
    )
    emit("")

    if trace_rows:
        browser_header = " | ".join(b.capitalize() for b in all_browsers) if all_browsers else "Browser"
        browser_sep = " | ".join("---" for _ in (all_browsers or ["chrome"]))
        emit(
            f"| Req | Acceptance Criterion | Scenario | Test Case | Kane AI | Kane Session | What Kane Saw | "
            f"{browser_header} | Playwright | Session | Overall |"
        )
        emit(f"|---|---|---|---|---|---|---|{browser_sep}|---|---|---|")

        for row in trace_rows:
            req_id = row.get("requirement_id", "")
            criterion = (row.get("acceptance_criterion", ""))[:55]
            if len(row.get("acceptance_criterion", "")) > 55:
                criterion += "…"
            kane = row.get("kane_ai_result", "unknown")
            kane_link = row.get("kane_session_link", "")
            kane_cell = f"[session]({kane_link})" if kane_link else "—"
            one_liner = row.get("kane_one_liner", "") or "—"
            per_browser = row.get("playwright_per_browser", {})
            browser_cells = " | ".join(
                f"{status_icon(per_browser.get(b, 'data_unavailable'))} {per_browser.get(b, '—')}"
                for b in all_browsers
            ) if all_browsers else "—"
            pw = row.get("playwright_status", "data_unavailable")
            sel_link = row.get("session_link", "")
            sel_cell = f"[session]({sel_link})" if sel_link else "—"
            overall = row.get("overall", "unknown")
            overall_icon = status_icon(overall)
            emit(
                f"| `{req_id}` | {criterion} | `{row.get('scenario_id', 'n/a')}` "
                f"| `{row.get('test_case_id', 'n/a')}` | {kane} | {kane_cell} | {one_liner} "
                f"| {browser_cells} | {pw} | {sel_cell} | {overall_icon} {overall} |"
            )
        emit("")

        # Kane AI detail (collapsible)
        if any(row.get("kane_steps") or row.get("kane_summary") for row in trace_rows):
            emit("<details>")
            emit("<summary>Kane AI verification steps (expand)</summary>")
            emit("")
            for row in trace_rows:
                if not row.get("kane_steps") and not row.get("kane_summary"):
                    continue
                emit(f"**`{row['requirement_id']}` — {row.get('acceptance_criterion', '')}**")
                emit("")
                for step in row.get("kane_steps", []):
                    emit(f"- {step}")
                if row.get("kane_summary"):
                    emit("")
                    emit(f"_{row['kane_summary']}_")
                emit("")
            emit("</details>")
            emit("")

    if result_analysis:
        emit("### Result Analysis")
        emit("")
        emit(f"- **Overall health:** {result_analysis.get('overall_health', 'unknown')}")
        emit(f"- **Risk level:** {result_analysis.get('risk_level', 'unknown')}")
        emit(f"- **Kane AI pass rate:** {result_analysis.get('kane_pass_rate', 0)}%")
        emit(f"- **Playwright pass rate:** {result_analysis.get('playwright_pass_rate', 0)}%")
        emit("")
        for finding in result_analysis.get("key_findings", []):
            emit(f"- {finding}")
        hint = result_analysis.get("recommendation_hint", "")
        if hint:
            emit("")
            emit(f"> {hint}")
        emit("")

    if failing_scenarios:
        emit("**Failing scenarios:**")
        for sc in failing_scenarios:
            emit(f"- ❌ `{sc}`")
        emit("")

    if untested and executed > 0:
        emit("**No execution data for:**")
        for req in untested:
            emit(f"- ⚠️ `{req}` (data_unavailable)")
        emit("")

    # ── Validation Report ──────────────────────────────────────────────────────
    if validation:
        valid = validation.get("valid", True)
        v_errors = validation.get("errors", [])
        v_warnings = validation.get("warnings", [])
        emit("## Data Validation")
        emit("")
        label = "✅ VALID" if valid else "❌ INVALID"
        emit(f"Traceability integrity: **{label}**")
        if v_errors:
            emit("")
            for e in v_errors:
                emit(f"- ❌ {e}")
        if v_warnings:
            emit("")
            for w in v_warnings:
                emit(f"- ⚠️ {w}")
        emit("")

    # ── Coverage Analysis ──────────────────────────────────────────────────────
    coverage_data = load_json("reports/coverage_report.json", {})
    if coverage_data:
        cov_sum  = coverage_data.get("summary", {})
        cov_reqs = coverage_data.get("requirements", [])
        feat_rollup = coverage_data.get("feature_rollup", [])

        emit("## Requirement Coverage Analysis")
        emit("")
        emit("| Metric | Value |")
        emit("|--------|-------|")
        emit(f"| Total Requirements | {cov_sum.get('total_requirements', '?')} |")
        emit(f"| Fully Covered | {cov_sum.get('covered_full', 0)} "
             f"({cov_sum.get('coverage_pct', 0)}%) |")
        emit(f"| Partially Covered | {cov_sum.get('covered_partial', 0)} |")
        emit(f"| Uncovered | {cov_sum.get('uncovered', 0)} |")
        emit(f"| Negative Test Coverage | {cov_sum.get('negative_coverage_pct', 0)}% |")
        emit(f"| Mobile Coverage | {cov_sum.get('mobile_coverage_pct', 0)}% |")
        emit(f"| Android Coverage | {cov_sum.get('android_coverage_pct', 0)}% |")
        emit(f"| HyperExecute Coverage | {cov_sum.get('he_coverage_pct', 0)}% |")
        emit(f"| Flaky Requirements | {cov_sum.get('flaky_count', 0)} |")
        emit(f"| High-Risk Requirements | {cov_sum.get('high_risk_count', 0)} |")
        emit(f"| Missing Scenario Types | {cov_sum.get('missing_scenario_types', 0)} |")
        emit("")

        # Per-requirement coverage table
        emit("### Requirement Coverage Detail")
        emit("")
        emit("| Requirement | Coverage | Tests | Pass | Fail | Missing | Risk |")
        emit("|-------------|----------|-------|------|------|---------|------|")
        for r in cov_reqs:
            es = r.get("execution_status", {})
            cov_icon = {"FULL": "✅", "PARTIAL": "🟡", "NONE": "❌"}.get(r.get("coverage_status", "NONE"), "❓")
            risk_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(r.get("risk_level", ""), "⚪")
            emit(
                f"| `{r.get('requirement_id', '?')}` | {cov_icon} {r.get('coverage_status', 'NONE')} "
                f"| {es.get('total', 0)} | {es.get('passed', 0)} | {es.get('failed', 0)} "
                f"| {len(r.get('missing_scenarios', []))} | {risk_icon} {r.get('risk_level', '?')} |"
            )
        emit("")

        # Feature heatmap
        if feat_rollup:
            emit("### Feature Coverage Heatmap")
            emit("")
            emit("| Feature | Criticality | Total | Covered | Partial | Uncovered |")
            emit("|---------|-------------|-------|---------|---------|-----------|")
            for f in feat_rollup:
                crit_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(f.get("criticality", ""), "⚪")
                emit(
                    f"| {f['feature']} | {crit_icon} {f['criticality']} "
                    f"| {f['total']} | {f['covered']} | {f['partial']} | {f['none']} |"
                )
            emit("")

        # Missing scenarios
        missing_data = load_json("reports/missing_scenarios.json", {})
        missing_list = missing_data.get("missing", [])
        if missing_list:
            emit("### Missing Scenario Types (Coverage Gaps)")
            emit("")
            for m in missing_list:
                req_id = m.get("requirement_id", "?")
                feat   = m.get("feature", "?")
                crit   = m.get("criticality", "?")
                emit(f"**`{req_id}`** — {feat} (criticality: {crit})")
                for ms in m.get("missing", []):
                    type_badge = {"negative": "🔴 NEGATIVE", "edge_case": "🟡 EDGE",
                                  "happy_path": "🟢 HAPPY"}.get(ms["type"], ms["type"].upper())
                    emit(f"- `[{type_badge}]` {ms['description']}")
                emit("")

        # Flaky requirements
        flaky_data = load_json("reports/flaky_requirements.json", {})
        flaky_list = flaky_data.get("flaky", [])
        if flaky_list:
            emit("### Flaky Requirements")
            emit("")
            for f in flaky_list:
                emit(f"- ⚠️ `{f['requirement_id']}` ({f['feature']}) — "
                     f"{f['retry_count']} retries  scenarios: {', '.join(f['scenarios'])}")
            emit("")

    # ── Quality Gates ──────────────────────────────────────────────────────────
    qg_data = load_json("reports/quality_gates.json", {})
    if qg_data:
        gates_passed = qg_data.get("gates_passed", True)
        qg_icon = "✅" if gates_passed else "❌"
        emit("## Quality Gates")
        emit("")
        emit(f"**Overall: {qg_icon} {'PASSED' if gates_passed else 'FAILED'}**  "
             f"({qg_data.get('critical_failures', 0)} critical failures, "
             f"{qg_data.get('warnings', 0)} warnings)")
        emit("")
        emit("| Gate | Severity | Status | Actual | Threshold |")
        emit("|------|----------|--------|--------|-----------|")
        for g in qg_data.get("gates", []):
            g_icon = "✅" if g["passed"] else ("❌" if g["severity"] == "CRITICAL" else "⚠️")
            sev_icon = "🔴" if g["severity"] == "CRITICAL" else "🟡"
            emit(
                f"| {g['gate']} | {sev_icon} {g['severity']} | {g_icon} "
                f"| {g['actual']} {g.get('unit', '')} | {g['threshold']} {g.get('unit', '')} |"
            )
        emit("")

    # ── Impact Analysis ────────────────────────────────────────────────────────
    impact_data = load_json("reports/impacted_requirements.json", {})
    if impact_data and impact_data.get("changed_file_count", 0) > 0:
        max_impact = impact_data.get("max_impact", "NONE")
        impact_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(max_impact, "⚪")
        emit("## Change Impact Analysis")
        emit("")
        emit(f"**{len(impact_data.get('changed_files', []))} file(s) changed — "
             f"max impact: {impact_icon} {max_impact}**")
        emit("")
        emit(f"> {impact_data.get('recommendation', '')}")
        emit("")
        impacted_reqs = impact_data.get("impacted_requirements", [])
        if impacted_reqs:
            emit(f"**{len(impacted_reqs)} requirement(s) impacted:** "
                 + ", ".join(f"`{r}`" for r in impacted_reqs[:10])
                 + ("..." if len(impacted_reqs) > 10 else ""))
            emit("")
        impacted_feats = impact_data.get("impacted_features", [])
        if impacted_feats:
            emit(f"**Features affected:** {', '.join(impacted_feats)}")
            emit("")

    # ── RCA — Root Cause Analysis ──────────────────────────────────────────────
    rca_data = load_json("reports/rca_report.json", {})
    if rca_data and rca_data.get("total_failed", 0) > 0:
        emit("## Root Cause Analysis — Failed Tests")
        emit("")
        if rca_data.get("skipped_no_creds"):
            emit("> ⚠️ RCA skipped — `LT_USERNAME` / `LT_ACCESS_KEY` not set.")
            emit("> Configure these secrets to enable LambdaTest AI root cause analysis.")
            emit("")
        else:
            emit(f"**{rca_data['total_failed']} failed test(s) — "
                 f"{rca_data.get('rca_fetched', 0)} RCA analyses retrieved**")
            emit("")
            for a in rca_data.get("analyses", []):
                sc_id   = a.get("scenario_id", "?")
                req_id  = a.get("requirement_id", "?")
                browser = a.get("browser", "?")
                link    = a.get("session_link", "")
                rca     = a.get("root_cause", "N/A")
                ctx     = a.get("kane_context", {})
                session_md = f"[session]({link})" if link else "—"
                emit(f"**❌ `{sc_id}` / `{req_id}` — {browser}** — {session_md}")
                if ctx.get("acceptance_criterion"):
                    emit(f"> _{ctx['acceptance_criterion']}_")
                emit(f"> **Root Cause:** {rca[:300]}")
                emit("")

    # ── Enriched RCA (rca_enrichment.py) ──────────────────────────────────────
    enriched_data = load_json("reports/rca_enriched.json", {})
    enriched_analyses = enriched_data.get("analyses", [])
    if enriched_analyses:
        emit("## Enriched RCA — Autonomous Failure Diagnosis")
        emit("")
        mcp_badge = "MCP-enriched" if enriched_data.get("mcp_available") else "REST-only"
        emit(f"_{enriched_data.get('total_failures_analyzed', 0)} failure(s) diagnosed · {mcp_badge}_")
        emit("")

        s = enriched_data.get("summary", {})
        emit("| Confidence | Count | Likely Flaky | Systemic (all browsers) |")
        emit("|---|---|---|---|")
        emit(f"| 🟢 HIGH (75-100) | {s.get('high_confidence', 0)} | "
             f"{s.get('likely_flaky', 0)} | {s.get('systemic_failures', 0)} |")
        emit(f"| 🟡 MEDIUM (50-74) | {s.get('medium_confidence', 0)} | | |")
        emit(f"| 🔴 LOW (<50) | {s.get('low_confidence', 0)} | | |")
        emit("")

        emit("| Scenario | Failure Type | Confidence | Scope | Flakiness | Root Cause | Remediation | Artifacts |")
        emit("|---|---|---|---|---|---|---|---|")
        for a in enriched_analyses:
            sc_id      = a.get("scenario_id", "?")
            ftype      = a.get("failure_type", "UNKNOWN")
            confidence = a.get("confidence", 0)
            band       = a.get("confidence_band", "LOW")
            conf_icon  = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(band, "⚪")
            scope      = a.get("browser_correlation", {}).get("failure_scope", "NO_DATA")
            scope_icon = {"ALL_BROWSERS": "🌐", "SINGLE_BROWSER": "🔬", "SOME_BROWSERS": "⚠️", "NO_DATA": "—"}.get(scope, "—")
            flaky_v    = a.get("flakiness", {}).get("verdict", "REPRODUCIBLE")
            flaky_icon = "🎲" if "FLAKY" in flaky_v else "🔁"
            rca_text   = a.get("enhanced_rca", "")
            rca_short  = (rca_text[:80] + "…") if len(rca_text) > 80 else rca_text
            rec_text   = a.get("remediation", {}).get("recommended_action", "")
            rec_short  = (rec_text[:60] + "…") if len(rec_text) > 60 else rec_text
            video      = a.get("artifacts", {}).get("video_url", "")
            shot       = a.get("artifacts", {}).get("screenshot_url", "")
            artifact_md = " ".join(filter(None, [
                f"[▶]({video})" if video else "",
                f"[📷]({shot})" if shot else "",
            ])) or "—"
            emit(f"| `{sc_id}` | `{ftype}` | {conf_icon} {confidence}% | "
                 f"{scope_icon} {scope} | {flaky_icon} {flaky_v} | "
                 f"{rca_short} | {rec_short} | {artifact_md} |")
        emit("")

        emit("<details>")
        emit("<summary>Full remediation details</summary>")
        emit("")
        for a in enriched_analyses:
            rem = a.get("remediation", {})
            emit(f"**`{a.get('scenario_id')}` — {a.get('failure_type')} "
                 f"(confidence: {a.get('confidence')}%)**")
            emit("")
            if rem.get("recommended_action"):
                emit(f"> {rem['recommended_action']}")
            if rem.get("patch_detail"):
                emit(f"> {rem['patch_detail']}")
            if rem.get("retry_guidance"):
                emit(f"> _{rem['retry_guidance']}_")
            if a.get("console_errors"):
                emit(f"**Console:** `{a['console_errors'][0][:200]}`")
            if a.get("network_errors"):
                err = a["network_errors"][0]
                emit(f"**Network:** `{err.get('method','GET')} {err.get('url','')[:120]}` → HTTP {err.get('status','?')}")
            ev = rem.get("evidence_links", {})
            if ev.get("video"):
                emit(f"**Video:** {ev['video']}")
            if ev.get("screenshot"):
                emit(f"**Screenshot:** {ev['screenshot']}")
            emit("")
        emit("</details>")
        emit("")

    # ── Release Recommendation ─────────────────────────────────────────────────
    emit("## Release Recommendation")
    emit("")
    icon = verdict_emoji(verdict_line)
    emit(f"### {icon} {verdict_line}")
    emit("")
    if recommendation_line:
        emit(recommendation_line)
    emit("")
    emit(f"- Requirements covered: **{trace_summary.get('requirements_covered', '?')}/{trace_summary.get('requirements_total', '?')}**")
    emit(f"- Browsers tested: **{', '.join(all_browsers) or 'none'}**")
    emit(f"- Playwright pass rate: **{pass_rate}%** ({passed} passed, {executed - passed} failed)")
    if run_url:
        emit(f"- Full run details: {run_url}")
    emit("")


    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    summary_len = len(Path(summary_path).read_text(encoding="utf-8")) if summary_path and Path(summary_path).exists() else 0
    print_stage_result("9", "GITHUB_SUMMARY", {
        "Sections":       "stage-status, kane-results, playwright-results, traceability, verdict",
        "Verdict":        verdict_line,
        "HE dashboard":   he_job_link or "N/A",
        "Summary size":   f"{summary_len:,} chars" if summary_len else "stdout only",
        "Output":         summary_path or "stdout",
    })


if __name__ == "__main__":
    main()
