# FILE: d:\agentic-stlc\ci\self_healing.py
"""
Self-Healing Engine — Stage 8b of the Agentic STLC pipeline.

Reads failure_intelligence.json and applies autonomous patches to fix
identified failures. Modifies scenarios/scenarios.json and kane/objectives.json.
Records all patch operations in reports/self_healing_report.json and
reports/self_healing.md.

Usage:
    python ci/self_healing.py
"""

import json
import os
import re
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
SCENARIOS_DIR = REPO_ROOT / "scenarios"
KANE_DIR = REPO_ROOT / "kane"

FAILURE_INTELLIGENCE_PATH = REPORTS_DIR / "failure_intelligence.json"
SCENARIOS_PATH = SCENARIOS_DIR / "scenarios.json"
OBJECTIVES_PATH = KANE_DIR / "objectives.json"

OUTPUT_JSON_PATH = REPORTS_DIR / "self_healing_report.json"
OUTPUT_MD_PATH = REPORTS_DIR / "self_healing.md"
PLAYWRIGHT_PATCHES_PATH = REPORTS_DIR / "playwright_patches.json"

# ---------------------------------------------------------------------------
# Failure type constants (mirrors failure_intelligence.py — no cross-import)
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

# Patch types that write to scenarios.json
SCENARIOS_JSON_PATCH_TYPES = {KANE_WRONG_TASK, KANE_STEP_LIMIT}

# Patch types that write to objectives.json
OBJECTIVES_JSON_PATCH_TYPES = {AUTH_PREREQUISITE_MISSING}

# Patch types that write to playwright_patches.json
PLAYWRIGHT_PATCH_TYPES = {PLAYWRIGHT_SYNC_TIMING, PLAYWRIGHT_LOCATOR_FAILURE, PLAYWRIGHT_NAVIGATION_FAILURE}

# Patch types that cannot be auto-patched
SKIP_PATCH_TYPES = {APPLICATION_DEFECT, DATA_UNAVAILABLE, UNKNOWN_FAILURE}


# ---------------------------------------------------------------------------
# Helper: safe file loader
# ---------------------------------------------------------------------------
def _load_json(path: Path, default):
    if not path.exists():
        print(f"  [WARN] File not found: {path} — using default.")
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        print(f"  [WARN] JSON decode error in {path}: {exc} — using default.")
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Objective builders for specific failure types
# ---------------------------------------------------------------------------

def _build_kane_wrong_task_objective(failure: dict, scenario: dict) -> str:
    """
    Build a corrected Kane objective that starts with an explicit URL
    and ends with the stop directive.

    We attempt to reuse any product_id already encoded in the current objective,
    then construct a direct navigation-first objective.
    """
    current_obj = scenario.get("kane_objective", "")
    req_desc = (
        scenario.get("description")
        or scenario.get("source_description")
        or failure.get("failed_requirement", "")
    )

    # Extract product_id if present in existing objective
    product_id_match = re.search(r"product_id=(\d+)", current_obj)
    if product_id_match:
        product_id = product_id_match.group(1)
        base_url = (
            f"https://ecommerce-playground.lambdatest.io/index.php"
            f"?route=product/product&product_id={product_id}"
        )
    else:
        # Fall back to homepage with explicit note
        base_url = "https://ecommerce-playground.lambdatest.io/"

    # Build the action part from the requirement description (short form)
    req_short = req_desc.strip().rstrip(".")
    action_part = req_short if req_short else "verify the required feature"

    new_objective = (
        f"Navigate directly to {base_url} — "
        f"{action_part}. "
        f"Stop immediately once confirmed. Do not navigate further."
    )
    return new_objective


def _build_kane_step_limit_objective(failure: dict, scenario: dict) -> str:
    """
    Append the stop-immediately directive to the existing objective.
    If the directive is already present, return the existing objective unchanged.
    """
    current_obj = (scenario.get("kane_objective") or "").strip()
    stop_directive = "Stop immediately once confirmed. Do not navigate further."

    # Normalise: strip any existing trailing stop variants
    stop_patterns = [
        r"\.\s*Stop\s+immediately\s+once\s+confirmed\.?\s*Do\s+not\s+navigate\s+further\.?",
        r"\.\s*Stop\s+once\s+confirmed\.?",
        r"\.\s*Stop\s+immediately\s+once\s+\w[\w\s]+confirmed\.?",
    ]
    cleaned = current_obj
    for pat in stop_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).rstrip()

    # Ensure it ends with a period before appending
    if cleaned and not cleaned.endswith("."):
        cleaned += "."

    new_objective = f"{cleaned} {stop_directive}".strip() if cleaned else stop_directive
    return new_objective


def _build_auth_objective(failure: dict, scenario: dict) -> str:
    """
    Prepend a login step to the existing objective in objectives.json format.
    """
    current_obj = (scenario.get("kane_objective") or "").strip()
    login_prefix = (
        "Navigate to /account/login — log in with valid credentials — then "
    )

    # Avoid double-prepending
    if "account/login" in current_obj.lower():
        return current_obj

    if current_obj:
        # Make sure the existing objective starts lower-case for smooth joining
        joined_obj = current_obj[0].lower() + current_obj[1:] if current_obj else ""
        new_objective = f"{login_prefix}{joined_obj}"
    else:
        new_objective = f"{login_prefix}verify the required feature. Stop once confirmed."

    # Ensure stop directive
    if "stop" not in new_objective.lower():
        new_objective = new_objective.rstrip(".") + ". Stop once confirmed."

    return new_objective


# ---------------------------------------------------------------------------
# Patch appliers
# ---------------------------------------------------------------------------

class PatchResult:
    """Thin data holder for a single patch operation."""

    def __init__(
        self,
        scenario_id: str,
        failure_type: str,
        patch_type: str,
        original: str,
        patched: str,
        file_modified: str,
        status: str,
        skip_reason: str = "",
    ):
        self.scenario_id = scenario_id
        self.failure_type = failure_type
        self.patch_type = patch_type
        self.original = original
        self.patched = patched
        self.file_modified = file_modified
        self.status = status
        self.skip_reason = skip_reason

    def to_dict(self) -> dict:
        d = {
            "scenario_id": self.scenario_id,
            "failure_type": self.failure_type,
            "patch_type": self.patch_type,
            "original": self.original,
            "patched": self.patched,
            "file_modified": self.file_modified,
            "status": self.status,
        }
        if self.skip_reason:
            d["skip_reason"] = self.skip_reason
        return d


def apply_scenarios_json_patch(
    failure: dict,
    scenarios_map: dict[str, dict],
) -> PatchResult:
    """
    Patch scenarios/scenarios.json for KANE_WRONG_TASK or KANE_STEP_LIMIT.
    Mutates scenarios_map in-place.
    """
    sc_id = failure.get("failed_scenario", "")
    ftype = failure.get("failure_type", "")

    scenario = scenarios_map.get(sc_id)
    if not scenario:
        return PatchResult(
            scenario_id=sc_id,
            failure_type=ftype,
            patch_type="kane_objective",
            original="",
            patched="",
            file_modified="scenarios/scenarios.json",
            status="skipped",
            skip_reason=f"Scenario {sc_id} not found in scenarios.json",
        )

    original_obj = scenario.get("kane_objective", "")

    if ftype == KANE_WRONG_TASK:
        new_obj = _build_kane_wrong_task_objective(failure, scenario)
    elif ftype == KANE_STEP_LIMIT:
        new_obj = _build_kane_step_limit_objective(failure, scenario)
    else:
        return PatchResult(
            scenario_id=sc_id,
            failure_type=ftype,
            patch_type="kane_objective",
            original=original_obj,
            patched=original_obj,
            file_modified="scenarios/scenarios.json",
            status="skipped",
            skip_reason=f"Unsupported failure type for scenarios.json patch: {ftype}",
        )

    if new_obj == original_obj:
        return PatchResult(
            scenario_id=sc_id,
            failure_type=ftype,
            patch_type="kane_objective",
            original=original_obj,
            patched=new_obj,
            file_modified="scenarios/scenarios.json",
            status="skipped",
            skip_reason="Objective already contains the required correction.",
        )

    # Apply mutation
    scenario["kane_objective"] = new_obj
    scenario["updated_at"] = datetime.now(timezone.utc).isoformat()

    return PatchResult(
        scenario_id=sc_id,
        failure_type=ftype,
        patch_type="kane_objective",
        original=original_obj,
        patched=new_obj,
        file_modified="scenarios/scenarios.json",
        status="applied",
    )


def apply_objectives_json_patch(
    failure: dict,
    scenarios_map: dict[str, dict],
    objectives_list: list[dict],
) -> PatchResult:
    """
    Patch kane/objectives.json for AUTH_PREREQUISITE_MISSING.
    Mutates objectives_list in-place.
    """
    sc_id = failure.get("failed_scenario", "")
    ftype = failure.get("failure_type", "")

    scenario = scenarios_map.get(sc_id, {})

    # Find the matching objective record
    obj_record = None
    for rec in objectives_list:
        if rec.get("scenario_id") == sc_id:
            obj_record = rec
            break

    if obj_record is None:
        # Create a new entry if missing
        obj_record = {
            "scenario_id": sc_id,
            "test_case_id": scenario.get("test_case_id", ""),
            "objective": scenario.get("kane_objective", ""),
        }
        objectives_list.append(obj_record)

    original_obj = obj_record.get("objective", "")
    new_obj = _build_auth_objective(failure, scenario)

    if new_obj == original_obj:
        return PatchResult(
            scenario_id=sc_id,
            failure_type=ftype,
            patch_type="auth_objective",
            original=original_obj,
            patched=new_obj,
            file_modified="kane/objectives.json",
            status="skipped",
            skip_reason="Objective already includes login prerequisite.",
        )

    obj_record["objective"] = new_obj
    obj_record["patched_at"] = datetime.now(timezone.utc).isoformat()

    return PatchResult(
        scenario_id=sc_id,
        failure_type=ftype,
        patch_type="auth_objective",
        original=original_obj,
        patched=new_obj,
        file_modified="kane/objectives.json",
        status="applied",
    )


def apply_playwright_patch(
    failure: dict,
    playwright_patches: list[dict],
) -> PatchResult:
    """
    Record a Playwright patch suggestion in playwright_patches.json.
    Does NOT touch test_powerapps.py (auto-generated).
    """
    sc_id = failure.get("failed_scenario", "")
    ftype = failure.get("failure_type", "")
    remediation = failure.get("auto_remediation", {})

    patch_entry = {
        "scenario_id": sc_id,
        "failure_type": ftype,
        "recommended_fix": remediation.get("recommended_action", ""),
        "patch_detail": remediation.get("patch_detail", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Specific guidance per type
    if ftype == PLAYWRIGHT_SYNC_TIMING:
        patch_entry["insertion_hint"] = (
            "After any page.goto() or page.click() that triggers a page update, "
            "insert: page.wait_for_load_state('networkidle')"
        )
        patch_entry["template_snippet"] = "page.wait_for_load_state('networkidle')"
    elif ftype == PLAYWRIGHT_LOCATOR_FAILURE:
        patch_entry["insertion_hint"] = (
            "Replace brittle selectors with role-based selectors and add "
            "explicit wait_for(state='visible', timeout=15000) before interaction."
        )
        patch_entry["template_snippet"] = (
            "page.locator('SELECTOR').wait_for(state='visible', timeout=15000)"
        )
    elif ftype == PLAYWRIGHT_NAVIGATION_FAILURE:
        patch_entry["insertion_hint"] = (
            "Wrap page.goto() in try/except and retry once. "
            "Ensure authentication state if URL requires login."
        )
        patch_entry["template_snippet"] = (
            "try:\n"
            "    page.goto(url, timeout=30000)\n"
            "except Exception:\n"
            "    page.wait_for_timeout(2000)\n"
            "    page.goto(url, timeout=30000)"
        )

    playwright_patches.append(patch_entry)

    return PatchResult(
        scenario_id=sc_id,
        failure_type=ftype,
        patch_type="playwright_patch_suggestion",
        original="",
        patched=patch_entry["template_snippet"],
        file_modified="reports/playwright_patches.json",
        status="applied",
    )


# ---------------------------------------------------------------------------
# Main self-healing function
# ---------------------------------------------------------------------------
def run_self_healing() -> dict:
    print_stage_header(
        "8b",
        "Self-Healing",
        "Autonomous patch application based on Failure Intelligence report",
    )

    # ------------------------------------------------------------------
    # 1. Load artifacts
    # ------------------------------------------------------------------
    fi_data = _load_json(FAILURE_INTELLIGENCE_PATH, {})
    failures: list[dict] = fi_data.get("failures", [])

    if not failures:
        print("  No failures in failure_intelligence.json — nothing to patch.")
        return _build_empty_report()

    scenarios_list: list[dict] = _load_json(SCENARIOS_PATH, [])
    scenarios_map: dict[str, dict] = {
        s["id"]: s for s in scenarios_list if isinstance(s, dict) and "id" in s
    }

    objectives_list: list[dict] = _load_json(OBJECTIVES_PATH, [])

    # ------------------------------------------------------------------
    # 2. Apply patches
    # ------------------------------------------------------------------
    patch_results: list[PatchResult] = []
    playwright_patches: list[dict] = []

    scenarios_dirty = False
    objectives_dirty = False

    print(f"  Processing {len(failures)} failure(s)…")

    for failure in failures:
        ftype = failure.get("failure_type", UNKNOWN_FAILURE)
        sc_id = failure.get("failed_scenario", "SC-???")

        print(f"  [{sc_id}] failure_type={ftype}")

        if ftype in SKIP_PATCH_TYPES:
            patch_results.append(PatchResult(
                scenario_id=sc_id,
                failure_type=ftype,
                patch_type="none",
                original="",
                patched="",
                file_modified="none",
                status="skipped",
                skip_reason=f"Failure type '{ftype}' requires manual investigation.",
            ))
            continue

        if ftype in SCENARIOS_JSON_PATCH_TYPES:
            result = apply_scenarios_json_patch(failure, scenarios_map)
            if result.status == "applied":
                scenarios_dirty = True
            patch_results.append(result)

        elif ftype in OBJECTIVES_JSON_PATCH_TYPES:
            result = apply_objectives_json_patch(failure, scenarios_map, objectives_list)
            if result.status == "applied":
                objectives_dirty = True
            patch_results.append(result)

        elif ftype in PLAYWRIGHT_PATCH_TYPES:
            result = apply_playwright_patch(failure, playwright_patches)
            patch_results.append(result)

        else:
            # UNKNOWN_FAILURE or anything else
            patch_results.append(PatchResult(
                scenario_id=sc_id,
                failure_type=ftype,
                patch_type="none",
                original="",
                patched="",
                file_modified="none",
                status="skipped",
                skip_reason=f"No patch handler for failure type: {ftype}",
            ))

    # ------------------------------------------------------------------
    # 3. Persist modified files
    # ------------------------------------------------------------------
    if scenarios_dirty:
        # Reconstruct the list from the map, preserving original order
        updated_scenarios = []
        seen_ids = set()
        for sc in scenarios_list:
            sid = sc.get("id")
            if sid in scenarios_map:
                updated_scenarios.append(scenarios_map[sid])
                seen_ids.add(sid)
            else:
                updated_scenarios.append(sc)
        # Append any new scenarios added to the map that weren't in the original list
        for sid, sc in scenarios_map.items():
            if sid not in seen_ids:
                updated_scenarios.append(sc)

        _save_json(SCENARIOS_PATH, updated_scenarios)
        print(f"  Updated: {SCENARIOS_PATH}")

    if objectives_dirty:
        _save_json(OBJECTIVES_PATH, objectives_list)
        print(f"  Updated: {OBJECTIVES_PATH}")

    if playwright_patches:
        _save_json(PLAYWRIGHT_PATCHES_PATH, {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_patches": len(playwright_patches),
            "note": (
                "These are patch suggestions only. "
                "test_powerapps.py is auto-generated and cannot be edited directly. "
                "Apply these changes to the Playwright body templates in ci/agent.py."
            ),
            "patches": playwright_patches,
        })
        print(f"  Written: {PLAYWRIGHT_PATCHES_PATH}")

    # ------------------------------------------------------------------
    # 4. Build report payload
    # ------------------------------------------------------------------
    patches_applied = sum(1 for r in patch_results if r.status == "applied")
    patches_skipped = sum(1 for r in patch_results if r.status == "skipped")
    patched_scenarios = [r.scenario_id for r in patch_results if r.status == "applied"]
    rerun_scenarios = list(dict.fromkeys(patched_scenarios))  # deduplicated, order preserved

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "patches_applied": patches_applied,
        "patches_skipped": patches_skipped,
        "patched_scenarios": patched_scenarios,
        "patch_details": [r.to_dict() for r in patch_results],
        "requires_rerun": len(rerun_scenarios) > 0,
        "rerun_scenarios": rerun_scenarios,
    }

    return report


def _build_empty_report() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "patches_applied": 0,
        "patches_skipped": 0,
        "patched_scenarios": [],
        "patch_details": [],
        "requires_rerun": False,
        "rerun_scenarios": [],
    }


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------
def build_markdown(report: dict) -> str:
    lines: list[str] = []

    patches_applied = report.get("patches_applied", 0)
    rerun_scenarios = report.get("rerun_scenarios", [])
    patch_details = report.get("patch_details", [])
    ts = report.get("generated_at", "")

    lines.append("# Self-Healing Report")
    lines.append("")
    lines.append(
        f"_{patches_applied} patches applied — "
        f"{len(rerun_scenarios)} scenario(s) require pipeline rerun_"
    )
    lines.append("")
    lines.append(f"_Generated: {ts}_")
    lines.append("")

    if not patch_details:
        lines.append("_No patches were applied._")
        return "\n".join(lines)

    applied = [d for d in patch_details if d.get("status") == "applied"]
    skipped = [d for d in patch_details if d.get("status") == "skipped"]

    if applied:
        lines.append("## Patches Applied")
        lines.append("")
        for detail in applied:
            sc_id = detail.get("scenario_id", "SC-???")
            ftype = detail.get("failure_type", "")
            ptype = detail.get("patch_type", "")
            original = detail.get("original", "")
            patched = detail.get("patched", "")
            file_mod = detail.get("file_modified", "")

            lines.append(f"### {sc_id} — {ftype} → {ptype.replace('_', ' ').title()}")
            lines.append("")
            if original:
                lines.append(f"**Original:** {original}")
            if patched:
                lines.append(f"**Patched:** {patched}")
            lines.append(f"**File:** `{file_mod}`")
            lines.append("")
            lines.append("---")
            lines.append("")

    if skipped:
        lines.append("## Patches Skipped")
        lines.append("")
        for detail in skipped:
            sc_id = detail.get("scenario_id", "SC-???")
            ftype = detail.get("failure_type", "")
            reason = detail.get("skip_reason", "No reason given.")
            lines.append(f"- **{sc_id}** ({ftype}): {reason}")
        lines.append("")

    if rerun_scenarios:
        lines.append("## Rerun Required")
        lines.append("")
        lines.append(
            "The following scenarios were patched and must be re-executed "
            "in the next pipeline run:"
        )
        lines.append("")
        for sc in rerun_scenarios:
            lines.append(f"- `{sc}`")
        lines.append("")
        lines.append(
            "> Set `FULL_RUN=false` and push — the pipeline will run only "
            "these updated scenarios via incremental selection."
        )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    report = run_self_healing()

    _save_json(OUTPUT_JSON_PATH, report)
    print(f"  Written: {OUTPUT_JSON_PATH}")

    md_content = build_markdown(report)
    with open(OUTPUT_MD_PATH, "w", encoding="utf-8") as fh:
        fh.write(md_content)
    print(f"  Written: {OUTPUT_MD_PATH}")

    patches_applied = report["patches_applied"]
    patches_skipped = report["patches_skipped"]
    rerun_scenarios = report["rerun_scenarios"]

    print_stage_result(
        "8b",
        "Self-Healing",
        {
            "Patches applied": patches_applied,
            "Patches skipped": patches_skipped,
            "Scenarios requiring rerun": len(rerun_scenarios),
            "Rerun scenarios": ", ".join(rerun_scenarios) if rerun_scenarios else "none",
            "Output JSON": str(OUTPUT_JSON_PATH.name),
            "Output Markdown": str(OUTPUT_MD_PATH.name),
        },
        success=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
