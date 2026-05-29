# FILE: d:\agentic-stlc\ci\failure_intelligence.py
"""
Failure Intelligence Engine — Stage 8a of the Agentic STLC pipeline.

Reads multi-source artifacts (Kane AI results, Playwright normalized results,
traceability matrix, RCA API report) and produces a classified failure analysis
with auto-remediation recommendations.

Usage:
    python ci/failure_intelligence.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap path so that stage_utils is importable when invoked from repo root
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from stage_utils import print_stage_header, print_stage_result  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = _SCRIPT_DIR.parent
REPORTS_DIR = REPO_ROOT / "reports"
REQUIREMENTS_DIR = REPO_ROOT / "requirements"
SCENARIOS_DIR = REPO_ROOT / "scenarios"

ANALYZED_REQUIREMENTS_PATH = REQUIREMENTS_DIR / "analyzed_requirements.json"
SCENARIOS_PATH = SCENARIOS_DIR / "scenarios.json"
NORMALIZED_RESULTS_PATH = REPORTS_DIR / "normalized_results.json"
TRACEABILITY_MATRIX_PATH = REPORTS_DIR / "traceability_matrix.json"
RCA_REPORT_PATH = REPORTS_DIR / "rca_report.json"

OUTPUT_JSON_PATH = REPORTS_DIR / "failure_intelligence.json"
OUTPUT_MD_PATH = REPORTS_DIR / "failure_intelligence.md"

# ---------------------------------------------------------------------------
# Failure type constants
# ---------------------------------------------------------------------------
KANE_WRONG_TASK = "KANE_WRONG_TASK"
KANE_STEP_LIMIT = "KANE_STEP_LIMIT"
AUTH_PREREQUISITE_MISSING = "AUTH_PREREQUISITE_MISSING"
PLAYWRIGHT_LOCATOR_FAILURE = "PLAYWRIGHT_LOCATOR_FAILURE"
PLAYWRIGHT_SYNC_TIMING = "PLAYWRIGHT_SYNC_TIMING"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
PLAYWRIGHT_NAVIGATION_FAILURE = "PLAYWRIGHT_NAVIGATION_FAILURE"
APPLICATION_DEFECT = "APPLICATION_DEFECT"
UNKNOWN_FAILURE = "UNKNOWN_FAILURE"

# Failure types that can be auto-remediated (patch_target != "none")
AUTO_REMEDIABLE_TYPES = {
    KANE_WRONG_TASK,
    KANE_STEP_LIMIT,
    AUTH_PREREQUISITE_MISSING,
    PLAYWRIGHT_LOCATOR_FAILURE,
    PLAYWRIGHT_SYNC_TIMING,
    PLAYWRIGHT_NAVIGATION_FAILURE,
}

# ---------------------------------------------------------------------------
# Auth-related keyword triggers
# ---------------------------------------------------------------------------
AUTH_KEYWORDS = [
    "login",
    "log in",
    "logout",
    "log out",
    "wish list",
    "wishlist",
    "account dashboard",
]


# ---------------------------------------------------------------------------
# Helper: safe file loader
# ---------------------------------------------------------------------------
def _load_json(path: Path, default):
    """Load JSON from *path*, returning *default* on any error."""
    if not path.exists():
        print(f"  [WARN] File not found: {path} — using default value.")
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        print(f"  [WARN] JSON decode error in {path}: {exc} — using default value.")
        return default


# ---------------------------------------------------------------------------
# Helper: extract key terms from a requirement description
# ---------------------------------------------------------------------------
def _key_terms(description: str) -> list[str]:
    """
    Extract meaningful content words (>= 4 chars) from *description*, lower-cased.
    Short words and common stop words are filtered out.
    """
    STOP_WORDS = {
        "user", "can", "the", "and", "see", "from", "with",
        "their", "that", "this", "into", "have", "will", "its",
        "page", "site", "they", "able", "when", "then",
    }
    words = description.lower().replace("-", " ").split()
    return [w.strip(".,;:!?\"'()") for w in words
            if len(w) >= 4 and w.strip(".,;:!?\"'()") not in STOP_WORDS]


# ---------------------------------------------------------------------------
# Failure classifier
# ---------------------------------------------------------------------------
def classify_failure(
    requirement_description: str,
    kane_status: str,
    kane_one_liner: str,
    kane_summary: str,
    playwright_per_browser: dict[str, str],
    error_message: str,
    playwright_body: str,
) -> str:
    """
    Classify a single failing requirement into exactly one failure type.

    Evaluation order matters — more specific checks come first.
    """
    req_lower = requirement_description.lower()
    one_liner_lower = (kane_one_liner or "").lower()
    summary_lower = (kane_summary or "").lower()
    error_lower = (error_message or "").lower()

    # ------------------------------------------------------------------ #
    # DATA_UNAVAILABLE — no execution data at all
    # ------------------------------------------------------------------ #
    if all(v == "data_unavailable" for v in playwright_per_browser.values()):
        return DATA_UNAVAILABLE

    # ------------------------------------------------------------------ #
    # KANE_STEP_LIMIT — step file not found / incomplete step chain
    # ------------------------------------------------------------------ #
    if (
        "step file not found" in summary_lower
        or "step file not found" in one_liner_lower
        or ("step 1" in summary_lower and summary_lower.count("step") <= 2)
    ):
        return KANE_STEP_LIMIT

    # ------------------------------------------------------------------ #
    # KANE_WRONG_TASK — Kane performed an unrelated task
    # ------------------------------------------------------------------ #
    if kane_status == "failed":
        key_terms = _key_terms(requirement_description)
        matches = sum(1 for term in key_terms if term in one_liner_lower)
        if matches == 0 and key_terms:
            return KANE_WRONG_TASK

    # ------------------------------------------------------------------ #
    # AUTH_PREREQUISITE_MISSING
    # ------------------------------------------------------------------ #
    has_auth_keyword = any(kw in req_lower for kw in AUTH_KEYWORDS)
    playwright_any_failed = any(
        v == "failed" for v in playwright_per_browser.values()
    )
    playwright_login_start = playwright_body.lstrip().lower().startswith(
        ("login", "navigate to /account/login", "page.goto.*login")
    )
    if has_auth_keyword and (
        kane_status == "failed"
        or (playwright_any_failed and not playwright_login_start)
    ):
        return AUTH_PREREQUISITE_MISSING

    # ------------------------------------------------------------------ #
    # Playwright-specific classifications (when playwright actually failed)
    # ------------------------------------------------------------------ #
    if playwright_any_failed:
        # APPLICATION_DEFECT — assertion mismatch
        if any(
            kw in error_lower
            for kw in ("expected", "assertionerror", "assert ")
        ):
            return APPLICATION_DEFECT

        # PLAYWRIGHT_NAVIGATION_FAILURE — network / navigation errors
        if any(
            kw in error_lower
            for kw in ("net::", "err_", "navigation", "page.goto")
        ):
            return PLAYWRIGHT_NAVIGATION_FAILURE

        # PLAYWRIGHT_LOCATOR_FAILURE — element not found / strict mode
        if any(
            kw in error_lower
            for kw in ("timeout", "strict mode violation", "locator", "waiting for")
        ):
            if any(kw in error_lower for kw in ("locator", "strict mode violation", "waiting for")):
                return PLAYWRIGHT_LOCATOR_FAILURE
            # Pure timeout without locator reference = sync timing
            return PLAYWRIGHT_SYNC_TIMING

    return UNKNOWN_FAILURE


# ---------------------------------------------------------------------------
# Auto-remediation builder
# ---------------------------------------------------------------------------
def build_remediation(
    failure_type: str,
    scenario: dict,
    requirement_description: str,
    kane_one_liner: str,
) -> dict:
    """
    Return the auto_remediation dict for the given failure type.
    patch_detail is built from available context.
    """
    sc_id = scenario.get("id", "SC-???")
    req_short = requirement_description[:80].rstrip()

    if failure_type == KANE_WRONG_TASK:
        return {
            "recommended_action": (
                "Add explicit URL navigation to the Kane task override for this "
                "requirement. Start with the exact product/page URL instead of "
                "the homepage."
            ),
            "patch_target": "kane_task_override",
            "patch_detail": (
                f"For {sc_id}: begin the Kane objective with a direct URL "
                f"(e.g. https://ecommerce-playground.lambdatest.io/index.php?"
                f"route=product/product&product_id=28) so Kane lands on the "
                f"correct page immediately. The current one_liner was: "
                f"\"{kane_one_liner}\". "
                f"Objective should reflect: \"{req_short}\"."
            ),
        }

    if failure_type == KANE_STEP_LIMIT:
        existing_obj = scenario.get("kane_objective", "")
        return {
            "recommended_action": (
                "Add 'Stop immediately once confirmed. Do not navigate further.' "
                "to the Kane objective to prevent step limit exhaustion."
            ),
            "patch_target": "kane_objective",
            "patch_detail": (
                f"Append to existing objective: "
                f"\"Stop immediately once confirmed. Do not navigate further.\" "
                f"Current objective: \"{existing_obj}\""
            ),
        }

    if failure_type == AUTH_PREREQUISITE_MISSING:
        return {
            "recommended_action": (
                "Inject login prerequisite: navigate to /account/login, fill "
                "credentials, click Login, verify dashboard before the test action."
            ),
            "patch_target": "playwright_body",
            "patch_detail": (
                f"For {sc_id}: prepend login steps to the Playwright body. "
                f"Example: page.goto('https://ecommerce-playground.lambdatest.io"
                f"/index.php?route=account/login'), "
                f"page.fill('#input-email', username), "
                f"page.fill('#input-password', password), "
                f"page.click('input[type=submit]'), "
                f"page.wait_for_url('**/account/account')."
            ),
        }

    if failure_type == PLAYWRIGHT_LOCATOR_FAILURE:
        return {
            "recommended_action": (
                "Use page.locator().wait_for(state='visible', timeout=15000) "
                "before interaction. Try role-based or data-testid selectors."
            ),
            "patch_target": "playwright_body",
            "patch_detail": (
                f"For {sc_id}: replace brittle CSS/XPath selectors with "
                f"page.get_by_role() or page.get_by_text() equivalents. "
                f"Add explicit waits: "
                f"page.locator('selector').wait_for(state='visible', timeout=15000) "
                f"before any click() or fill() call."
            ),
        }

    if failure_type == PLAYWRIGHT_SYNC_TIMING:
        return {
            "recommended_action": (
                "Add page.wait_for_load_state('networkidle') after navigation/"
                "click that triggers a page update."
            ),
            "patch_target": "playwright_body",
            "patch_detail": (
                f"For {sc_id}: after any page.goto() or page.click() that "
                f"triggers a full page load or AJAX update, insert: "
                f"page.wait_for_load_state('networkidle'). "
                f"Also consider page.wait_for_timeout(500) for micro-animation delays."
            ),
        }

    if failure_type == PLAYWRIGHT_NAVIGATION_FAILURE:
        return {
            "recommended_action": (
                "Add retry logic around page.goto() or check if the URL "
                "requires authentication."
            ),
            "patch_target": "playwright_body",
            "patch_detail": (
                f"For {sc_id}: wrap page.goto() in a try/except and retry once "
                f"after 2 seconds. If the URL is behind auth, add a login step "
                f"before navigation. Check that the base URL env var is set correctly."
            ),
        }

    if failure_type == APPLICATION_DEFECT:
        return {
            "recommended_action": (
                "Application returned unexpected content. This is likely an "
                "application defect. Review the session video and backend logs."
            ),
            "patch_target": "none",
            "patch_detail": (
                f"For {sc_id}: the assertion failed against live application state. "
                f"No automated patch is possible — escalate to the development team "
                f"with the LambdaTest session video as evidence."
            ),
        }

    if failure_type == DATA_UNAVAILABLE:
        return {
            "recommended_action": (
                "No execution data. Verify HyperExecute received this test in "
                "pytest_selection.txt and BROWSERS env var is set correctly."
            ),
            "patch_target": "none",
            "patch_detail": (
                f"For {sc_id}: check reports/pytest_selection.txt contains the "
                f"test node ID. Re-run with FULL_RUN=true to force inclusion. "
                f"Ensure BROWSERS env var is populated in hyperexecute.yaml."
            ),
        }

    # UNKNOWN_FAILURE
    return {
        "recommended_action": (
            "Manual investigation required. Failure type could not be classified "
            "automatically. Review session logs and screenshots."
        ),
        "patch_target": "none",
        "patch_detail": (
            f"For {sc_id}: no classification matched. Inspect the LambdaTest "
            f"session video, HyperExecute task logs, and Kane session link for clues."
        ),
    }


# ---------------------------------------------------------------------------
# RCA lookup helpers
# ---------------------------------------------------------------------------
def _build_rca_index(rca_data: dict) -> dict[str, str]:
    """
    Return a mapping of session_id/test_id → root_cause string.

    Handles both rca_report shapes:
      { "analyses": [...] }
      { "failures": [...] }
    """
    index: dict[str, str] = {}

    def _ingest_list(items: list) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            root_cause = (
                item.get("root_cause")
                or item.get("rootCause")
                or item.get("rca")
                or item.get("failure_reason")
                or ""
            )
            if not root_cause:
                continue
            for key_field in ("session_id", "testID", "test_id", "id"):
                key = item.get(key_field)
                if key:
                    index[str(key)] = str(root_cause)
                    break

    for list_key in ("analyses", "failures"):
        entries = rca_data.get(list_key)
        if isinstance(entries, list):
            _ingest_list(entries)

    return index


def _lookup_rca(rca_index: dict[str, str], session_links: list[str]) -> str:
    """
    Given a list of LambdaTest session URLs, return the first RCA string found.
    """
    for link in session_links:
        # Extract testID from URL query string  ?testID=XXXX
        if "testID=" in link:
            test_id = link.split("testID=")[-1].split("&")[0]
            if test_id in rca_index:
                return rca_index[test_id]
        # Fallback: match against any value in index keys
        for key in rca_index:
            if key in link:
                return rca_index[key]
    return ""


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------
def run_failure_intelligence() -> dict:
    """
    Load all artifacts, classify failures, build evidence records, and
    return the full failure intelligence payload as a dict.
    """
    print_stage_header(
        "8a",
        "Failure Intelligence",
        "Multi-source failure classification and auto-remediation engine",
    )

    # ------------------------------------------------------------------
    # 1. Load artifacts
    # ------------------------------------------------------------------
    print("  Loading artifacts…")

    analyzed_reqs_raw = _load_json(ANALYZED_REQUIREMENTS_PATH, [])
    # analyzed_requirements.json can be a list or a dict with a "requirements" key
    if isinstance(analyzed_reqs_raw, dict):
        analyzed_reqs_list = analyzed_reqs_raw.get("requirements", [])
    else:
        analyzed_reqs_list = analyzed_reqs_raw

    scenarios_raw = _load_json(SCENARIOS_PATH, [])

    normalized_raw = _load_json(NORMALIZED_RESULTS_PATH, {"results": []})
    normalized_results: list[dict] = normalized_raw.get("results", [])

    traceability_raw = _load_json(TRACEABILITY_MATRIX_PATH, {"rows": []})
    traceability_rows: list[dict] = traceability_raw.get("rows", [])

    rca_raw = _load_json(RCA_REPORT_PATH, {})
    rca_index = _build_rca_index(rca_raw)

    # ------------------------------------------------------------------
    # 2. Build lookup maps
    # ------------------------------------------------------------------

    # AC-xxx → analyzed requirement dict
    req_by_id: dict[str, dict] = {
        r["id"]: r for r in analyzed_reqs_list if isinstance(r, dict) and "id" in r
    }

    # SC-xxx → scenario dict
    scenario_by_id: dict[str, dict] = {
        s["id"]: s for s in scenarios_raw if isinstance(s, dict) and "id" in s
    }

    # AC-xxx → traceability row (primary source for combined Kane + PW results)
    trace_by_ac: dict[str, dict] = {
        row["requirement_id"]: row
        for row in traceability_rows
        if isinstance(row, dict) and "requirement_id" in row
    }

    # SC-xxx → list of normalized_results records
    norm_by_sc: dict[str, list[dict]] = {}
    for rec in normalized_results:
        sc = rec.get("scenario_id")
        if sc:
            norm_by_sc.setdefault(sc, []).append(rec)

    # ------------------------------------------------------------------
    # 3. Identify failing requirements
    # ------------------------------------------------------------------
    # A requirement is failing if its overall traceability result is "failed"
    # or if Kane failed (even if Playwright passed — pipeline rule: both required)
    failing_rows: list[dict] = [
        row for row in traceability_rows
        if row.get("overall") == "failed"
        or row.get("kane_ai_result") == "failed"
    ]

    if not failing_rows:
        print("  No failing requirements detected — nothing to analyse.")
        return _build_empty_output()

    print(f"  Found {len(failing_rows)} failing requirement(s): "
          f"{[r['requirement_id'] for r in failing_rows]}")

    # ------------------------------------------------------------------
    # 4. Classify and collect evidence for each failure
    # ------------------------------------------------------------------
    failures: list[dict] = []

    for row in failing_rows:
        ac_id = row["requirement_id"]
        sc_id = row.get("scenario_id", "")

        scenario = scenario_by_id.get(sc_id, {})
        req = req_by_id.get(ac_id, {})

        requirement_description = (
            row.get("acceptance_criterion")
            or scenario.get("description")
            or scenario.get("source_description")
            or req.get("description")
            or ""
        )

        # ---- Kane evidence ----
        kane_status = row.get("kane_ai_result", "unknown")
        kane_one_liner = row.get("kane_one_liner", "")
        kane_summary = row.get("kane_summary", "")
        kane_summary_snippet = (kane_summary or "")[:200]

        # ---- Playwright evidence ----
        playwright_per_browser: dict[str, str] = row.get("playwright_per_browser", {})

        # Collect per-browser error messages from normalized_results
        error_messages: list[str] = []
        session_links: list[str] = []

        for norm_rec in norm_by_sc.get(sc_id, []):
            err = norm_rec.get("error_message") or ""
            if err:
                error_messages.append(err)
            link = norm_rec.get("session_link") or ""
            if link:
                session_links.append(link)

        # Also capture the primary session link from the traceability row
        primary_link = row.get("session_link") or row.get("kane_session_link") or ""
        if primary_link and primary_link not in session_links:
            session_links.insert(0, primary_link)

        error_message = " | ".join(filter(None, error_messages))

        # ---- Playwright body (for auth detection heuristic) ----
        # We don't have the actual rendered body here; use the objective as proxy
        playwright_body_proxy = scenario.get("kane_objective", "")

        # ---- Classify ----
        failure_type = classify_failure(
            requirement_description=requirement_description,
            kane_status=kane_status,
            kane_one_liner=kane_one_liner,
            kane_summary=kane_summary,
            playwright_per_browser=playwright_per_browser,
            error_message=error_message,
            playwright_body=playwright_body_proxy,
        )

        # ---- RCA lookup ----
        lt_rca = _lookup_rca(rca_index, session_links)

        # ---- Auto-remediation ----
        remediation = build_remediation(
            failure_type=failure_type,
            scenario=scenario,
            requirement_description=requirement_description,
            kane_one_liner=kane_one_liner,
        )

        failures.append({
            "failed_requirement": ac_id,
            "failed_scenario": sc_id,
            "failure_type": failure_type,
            "kane_status": kane_status,
            "kane_one_liner": kane_one_liner,
            "kane_summary_snippet": kane_summary_snippet,
            "playwright_status": playwright_per_browser,
            "error_message": error_message,
            "session_links": session_links,
            "lt_rca": lt_rca,
            "auto_remediation": remediation,
        })

    # ------------------------------------------------------------------
    # 5. Build aggregate statistics
    # ------------------------------------------------------------------
    failure_clusters: dict[str, list[str]] = {}
    summary_by_type: dict[str, dict] = {}

    for f in failures:
        ftype = f["failure_type"]
        sc = f["failed_scenario"]
        failure_clusters.setdefault(ftype, []).append(sc)
        if ftype not in summary_by_type:
            summary_by_type[ftype] = {
                "count": 0,
                "auto_remediable": ftype in AUTO_REMEDIABLE_TYPES,
            }
        summary_by_type[ftype]["count"] += 1

    total_failures = len(failures)
    classified = sum(1 for f in failures if f["failure_type"] != UNKNOWN_FAILURE)
    auto_remediable = sum(
        1 for f in failures if f["failure_type"] in AUTO_REMEDIABLE_TYPES
    )

    # Remediation priority: remediable first (sorted by SC ID), then the rest
    remediation_priority = [
        f["failed_scenario"]
        for f in sorted(
            failures,
            key=lambda x: (
                0 if x["failure_type"] in AUTO_REMEDIABLE_TYPES else 1,
                x["failed_scenario"],
            ),
        )
    ]

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_failures": total_failures,
        "classified": classified,
        "auto_remediable": auto_remediable,
        "failure_clusters": failure_clusters,
        "failures": failures,
        "summary_by_type": summary_by_type,
        "remediation_priority": remediation_priority,
    }

    return output


def _build_empty_output() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_failures": 0,
        "classified": 0,
        "auto_remediable": 0,
        "failure_clusters": {},
        "failures": [],
        "summary_by_type": {},
        "remediation_priority": [],
    }


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------
def build_markdown(payload: dict) -> str:
    """
    Render payload as a human-readable Markdown report.
    """
    lines: list[str] = []

    ts = payload.get("generated_at", "")
    total = payload.get("total_failures", 0)
    auto_rem = payload.get("auto_remediable", 0)
    classified = payload.get("classified", 0)

    auto_pct = f"{(auto_rem / total * 100):.0f}%" if total > 0 else "N/A"

    lines.append("# Failure Intelligence Report")
    lines.append("")
    lines.append(f"_Generated: {ts}_")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- Total failures: **{total}**")
    lines.append(f"- Classified: **{classified}** / {total}")
    lines.append(f"- Auto-remediable: **{auto_rem}** ({auto_pct})")
    lines.append("")

    clusters = payload.get("failure_clusters", {})
    if clusters:
        lines.append("- Failure clusters:")
        for ftype, scs in clusters.items():
            lines.append(f"  - `{ftype}`: {', '.join(scs)}")
    else:
        lines.append("- No failure clusters detected.")
    lines.append("")

    failures = payload.get("failures", [])
    if not failures:
        lines.append("_No failures to report._")
        return "\n".join(lines)

    lines.append("## Failure Analysis")
    lines.append("")

    for f in failures:
        sc_id = f.get("failed_scenario", "SC-???")
        ac_id = f.get("failed_requirement", "AC-???")
        ftype = f.get("failure_type", UNKNOWN_FAILURE)
        kane_status = f.get("kane_status", "unknown")
        kane_one_liner = f.get("kane_one_liner", "")
        kane_snippet = f.get("kane_summary_snippet", "")
        pw_status = f.get("playwright_status", {})
        error_msg = f.get("error_message", "")
        session_links = f.get("session_links", [])
        lt_rca = f.get("lt_rca", "")
        remediation = f.get("auto_remediation", {})

        lines.append(f"### {sc_id} — {ftype} ({ac_id})")
        lines.append("")

        # Playwright status inline
        if pw_status:
            pw_inline = " | ".join(f"{br}: {st}" for br, st in pw_status.items())
        else:
            pw_inline = "no data"

        lines.append(f"**Kane result:** {kane_status} — \"{kane_one_liner}\"")
        lines.append(f"**Playwright result:** {pw_inline}")

        if error_msg:
            lines.append(f"**Error:** `{error_msg[:300]}`")

        if kane_snippet:
            lines.append(f"**Evidence:** {kane_snippet}")

        if session_links:
            links_md = " | ".join(
                f"[Session {i+1}]({lnk})" for i, lnk in enumerate(session_links[:3])
            )
            lines.append(f"**Sessions:** {links_md}")

        if lt_rca:
            lines.append(f"**LambdaTest RCA:** {lt_rca}")

        rec_action = remediation.get("recommended_action", "")
        patch_target = remediation.get("patch_target", "none")
        patch_detail = remediation.get("patch_detail", "")

        lines.append("**Auto-remediation:**")
        lines.append(f"> {rec_action}")
        if patch_detail:
            lines.append(f"> {patch_detail}")
        lines.append(f"**Patch target:** `{patch_target}`")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    payload = run_failure_intelligence()

    # Write JSON
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print(f"  Written: {OUTPUT_JSON_PATH}")

    # Write Markdown
    md_content = build_markdown(payload)
    with open(OUTPUT_MD_PATH, "w", encoding="utf-8") as fh:
        fh.write(md_content)
    print(f"  Written: {OUTPUT_MD_PATH}")

    total = payload["total_failures"]
    auto_rem = payload["auto_remediable"]
    classified = payload["classified"]

    print_stage_result(
        "8a",
        "Failure Intelligence",
        {
            "Total failures": total,
            "Classified": classified,
            "Auto-remediable": auto_rem,
            "Output JSON": str(OUTPUT_JSON_PATH.name),
            "Output Markdown": str(OUTPUT_MD_PATH.name),
        },
        success=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
