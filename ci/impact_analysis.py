"""
Change Impact Analysis Engine.

Determines which requirements, scenarios, and Playwright specs are impacted
by recent file changes (from git diff or CI environment variables).

Sources:
  - Git diff (via subprocess, falling back through multiple strategies)
  - requirements/analyzed_requirements.json
  - scenarios/scenarios.json

Produces:
  - reports/impacted_requirements.json

Exit: always 0 — impact analysis is advisory, never blocks.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result


# ── File-to-feature impact map ────────────────────────────────────────────────
# Pattern (regex) → list of impacted features, impact level
FILE_IMPACT_MAP: list[dict] = [
    # Test files — touch these, everything could break
    {"pattern": r"tests/playwright/test_powerapps\.py",   "features": ["ALL"], "level": "CRITICAL"},
    {"pattern": r"tests/playwright/conftest\.py",         "features": ["ALL"], "level": "CRITICAL"},
    {"pattern": r"tests/",                                "features": ["ALL"], "level": "HIGH"},
    # CI orchestration
    {"pattern": r"ci/agent\.py",                         "features": ["ALL"], "level": "CRITICAL"},
    {"pattern": r"ci/analyze_requirements\.py",          "features": ["ALL"], "level": "CRITICAL"},
    {"pattern": r"ci/collect_kane_exports\.py",          "features": ["ALL"], "level": "HIGH"},
    {"pattern": r"ci/generate_tests_from_scenarios\.py", "features": ["ALL"], "level": "HIGH"},
    {"pattern": r"ci/normalize_artifacts\.py",           "features": ["ALL"], "level": "HIGH"},
    {"pattern": r"ci/build_traceability\.py",            "features": ["ALL"], "level": "MEDIUM"},
    {"pattern": r"ci/coverage_analysis\.py",             "features": ["ALL"], "level": "MEDIUM"},
    {"pattern": r"ci/quality_gates\.py",                 "features": ["ALL"], "level": "MEDIUM"},
    {"pattern": r"ci/",                                  "features": ["ALL"], "level": "MEDIUM"},
    # Requirements — changes here trigger re-analysis
    {"pattern": r"requirements/search\.txt",
     "features": ["SEARCH", "CART", "CATALOG", "AUTH", "CHECKOUT", "WISHLIST", "SORT"],
     "level": "CRITICAL"},
    {"pattern": r"requirements/cart\.txt",    "features": ["CART", "CHECKOUT"],        "level": "HIGH"},
    {"pattern": r"requirements/",             "features": ["ALL"],                     "level": "HIGH"},
    # Scenarios and Kane
    {"pattern": r"scenarios/scenarios\.json", "features": ["ALL"],                     "level": "HIGH"},
    {"pattern": r"kane/",                     "features": ["ALL"],                     "level": "HIGH"},
    # Infrastructure
    {"pattern": r"hyperexecute\.yaml",        "features": ["ALL"],                     "level": "HIGH"},
    {"pattern": r"\.github/workflows/",       "features": ["ALL"],                     "level": "MEDIUM"},
    {"pattern": r"requirements\.txt",         "features": ["ALL"],                     "level": "HIGH"},
    {"pattern": r"pytest\.ini",               "features": ["ALL"],                     "level": "MEDIUM"},
]

# ── Feature → requirement keyword mapping ────────────────────────────────────
FEATURE_REQ_KEYWORDS: dict[str, list[str]] = {
    "SEARCH":         ["search", "find product"],
    "CART":           ["cart", "add to cart", "remove from cart", "update quantity"],
    "CATALOG":        ["catalog", "browse", "product listing", "laptops"],
    "FILTER":         ["filter", "manufacturer", "brand"],
    "PRODUCT_DETAIL": ["product detail", "detail page", "product name"],
    "GUEST":          ["guest", "without logging"],
    "AUTH":           ["register", "log in", "login", "log out", "logout", "account"],
    "CHECKOUT":       ["checkout", "shipping", "flat rate"],
    "WISHLIST":       ["wish list", "wishlist"],
    "SORT":           ["sort", "price low"],
}

_LEVEL_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


def _load_json(path: str, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _get_changed_files() -> list[str]:
    """
    Detect changed files using multiple strategies in priority order.
    Returns a (possibly empty) list of file paths.
    """
    # Strategy 1: GitHub Actions pull-request diff against base branch
    base_ref = os.environ.get("GITHUB_BASE_REF", "")
    if base_ref:
        try:
            r = subprocess.run(
                ["git", "diff", "--name-only", f"origin/{base_ref}...HEAD"],
                capture_output=True, text=True, check=False, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                return [f.strip() for f in r.stdout.strip().splitlines() if f.strip()]
        except Exception:
            pass

    # Strategy 2: Last commit
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return [f.strip() for f in r.stdout.strip().splitlines() if f.strip()]
    except Exception:
        pass

    # Strategy 3: Working-tree changes vs HEAD
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return [f.strip() for f in r.stdout.strip().splitlines() if f.strip()]
    except Exception:
        pass

    return []


def _match_rules(changed_file: str) -> list[dict]:
    return [rule for rule in FILE_IMPACT_MAP
            if re.search(rule["pattern"], changed_file, re.IGNORECASE)]


def _reqs_for_features(features: list[str], requirements: list[dict]) -> list[str]:
    if "ALL" in features:
        return [r["id"] for r in requirements]
    matched = []
    for req in requirements:
        desc = req.get("description", "").lower()
        for feat in features:
            if any(kw in desc for kw in FEATURE_REQ_KEYWORDS.get(feat, [feat.lower()])):
                matched.append(req["id"])
                break
    return matched


def _scenarios_for_reqs(req_ids: set, scenarios: list[dict]) -> list[str]:
    return [s["id"] for s in scenarios if s.get("requirement_id") in req_ids]


def _impact_recommendation(level: str, req_count: int) -> str:
    if level == "CRITICAL":
        return (f"FULL regression required — {req_count} requirement(s) impacted by "
                "critical file changes. Run all scenarios.")
    if level == "HIGH":
        return (f"Targeted regression recommended — {req_count} requirement(s) potentially "
                "impacted. Run affected scenarios.")
    if level == "MEDIUM":
        return (f"Smoke test recommended — {req_count} requirement(s) may be indirectly "
                "affected. Run at minimum the impacted feature tests.")
    return "No significant functional impact detected — incremental run is sufficient."


def analyze(
    requirements_path: str = "requirements/analyzed_requirements.json",
    scenarios_path:    str = "scenarios/scenarios.json",
) -> dict:
    print_stage_header("7c", "IMPACT_ANALYSIS",
                        "Determine which requirements are impacted by recent changes")
    Path("reports").mkdir(exist_ok=True)

    requirements  = _load_json(requirements_path, [])
    scenarios     = _load_json(scenarios_path, [])
    changed_files = _get_changed_files()

    if not changed_files:
        print("[impact_analysis] No changed files detected — skipping impact mapping")
        result = {
            "generated_at":          datetime.now(timezone.utc).isoformat(),
            "changed_files":         [],
            "changed_file_count":    0,
            "impacted_features":     [],
            "impacted_requirements": [],
            "impacted_scenarios":    [],
            "impacted_spec_files":   [],
            "max_impact":            "NONE",
            "recommendation":        "No changed files detected — no impact analysis possible.",
        }
        Path("reports/impacted_requirements.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        print_stage_result("7c", "IMPACT_ANALYSIS", {
            "Changed files":         0,
            "Impacted requirements": 0,
            "Impacted scenarios":    0,
            "Max impact level":      "NONE",
            "Output":                "reports/impacted_requirements.json",
        })
        return result

    impacted_features: set[str] = set()
    impact_levels: list[str]    = []
    file_details: list[dict]    = []

    for cf in changed_files:
        rules = _match_rules(cf)
        if not rules:
            file_details.append({"file": cf, "impact": "LOW", "features": ["GENERAL"]})
            impact_levels.append("LOW")
            continue
        max_rule = max(rules, key=lambda r: _LEVEL_ORDER.get(r["level"], 0))
        all_features = list({f for r in rules for f in r["features"]})
        file_details.append({
            "file":     cf,
            "impact":   max_rule["level"],
            "features": all_features,
        })
        impacted_features.update(all_features)
        impact_levels.append(max_rule["level"])

    max_impact = max(impact_levels, key=lambda x: _LEVEL_ORDER.get(x, 0)) if impact_levels else "LOW"

    impacted_req_ids  = list(dict.fromkeys(
        _reqs_for_features(list(impacted_features), requirements)
    ))
    impacted_sc_ids   = _scenarios_for_reqs(set(impacted_req_ids), scenarios)
    spec_files        = ["tests/playwright/test_powerapps.py"] if impacted_req_ids else []

    result = {
        "generated_at":          datetime.now(timezone.utc).isoformat(),
        "changed_files":         file_details,
        "changed_file_count":    len(changed_files),
        "impacted_features":     sorted(impacted_features - {"ALL"}),
        "impacted_requirements": impacted_req_ids,
        "impacted_scenarios":    impacted_sc_ids,
        "impacted_spec_files":   spec_files,
        "max_impact":            max_impact,
        "recommendation":        _impact_recommendation(max_impact, len(impacted_req_ids)),
    }

    Path("reports/impacted_requirements.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )

    print_stage_result("7c", "IMPACT_ANALYSIS", {
        "Changed files":         len(changed_files),
        "Impacted features":     len(impacted_features - {"ALL"}),
        "Impacted requirements": len(impacted_req_ids),
        "Impacted scenarios":    len(impacted_sc_ids),
        "Max impact level":      max_impact,
        "Output":                "reports/impacted_requirements.json",
    })
    return result


if __name__ == "__main__":
    analyze()
