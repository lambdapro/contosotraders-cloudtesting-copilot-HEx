import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result

# Optimised Kane objectives: map canonical description substrings → concise, termination-aware objective.
# Kept in sync with _KANE_TASK_OVERRIDES in analyze_requirements.py.
_OBJECTIVE_OVERRIDES: dict[str, str] = {
    "add a product to the cart from the product detail page": (
        "Navigate to product_id=28 — click Add to Cart — verify cart icon count updates. Stop once confirmed."
    ),
    "open the cart dropdown and see the list of added items": (
        "Navigate to product_id=28 — add to cart — open cart dropdown — verify item name and price. Stop once confirmed."
    ),
    "log in with a registered email address and password and land on the account dashboard": (
        "Navigate to /account/login — enter credentials — click Login — verify dashboard URL. Stop immediately once confirmed."
    ),
    "log out from the account and be redirected to the home page": (
        "Log in — click My Account dropdown — click Logout — verify home page redirect. Stop once confirmed."
    ),
    "remove an item from the shopping cart and see the cart update with the item gone": (
        "Navigate to product_id=28 — add to cart — navigate to checkout/cart — click Remove — verify cart empty. Stop once confirmed."
    ),
    "update the quantity of an item in the shopping cart and see the line total recalculate": (
        "Navigate to product_id=28 — add to cart — navigate to checkout/cart — change qty to 2 — update — verify total recalculates. Stop once confirmed."
    ),
    "add a product to the wish list from the product detail page and view it in the wishlist": (
        "Log in — navigate to product_id=40 — click Add to Wish List — verify confirmation. Stop once wishlist confirmed."
    ),
    "complete a guest checkout by entering a shipping address and selecting flat rate shipping": (
        "Navigate to product_id=28 — add to cart — navigate to checkout — select Guest Checkout — fill address — select Flat Rate — verify proceed. Stop once shipping confirmed."
    ),
}


def _get_kane_objective(description: str) -> str:
    dl = description.lower()
    for keyword, objective in _OBJECTIVE_OVERRIDES.items():
        if keyword in dl:
            return objective
    return description


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", default="requirements/analyzed_requirements.json")
    parser.add_argument("--scenarios", default="scenarios/scenarios.json")
    return parser.parse_args()


def load_json(path, default):
    file_path = Path(path)
    if not file_path.exists():
        return default
    content = file_path.read_text(encoding="utf-8").strip()
    if not content:
        return default
    return json.loads(content)


def title_and_steps(requirement):
    """
    Build scenario title, steps, and expected result.

    Primary source: Kane AI's NDJSON run_end / step_end output stored on the
    analyzed requirement (kane_one_liner, kane_steps, kane_summary).
    Falls back to keyword-based defaults when Kane run was skipped or failed.
    """
    one_liner = requirement.get("kane_one_liner", "").strip()
    kane_steps = [s for s in requirement.get("kane_steps", []) if s.strip()]
    summary = requirement.get("kane_summary", "").strip()
    description = requirement["description"]

    title = one_liner if one_liner else _fallback_title(description)
    steps = kane_steps if kane_steps else _fallback_steps(description)
    expected = summary if summary else _fallback_expected(description)

    return title, steps, expected


def _fallback_title(description):
    words = description.replace(".", "").replace(":", "").split()
    return " ".join(words[:10]).capitalize()


def _fallback_steps(description):
    lowered = description.lower()
    if "filter" in lowered or "refine" in lowered:
        return [
            "Navigate to https://ecommerce-playground.lambdatest.io/",
            "Go to a category page",
            "Select a brand filter from the sidebar",
            "Verify the product list updates",
        ]
    if "click" in lowered and ("detail" in lowered or "price" in lowered):
        return [
            "Navigate to https://ecommerce-playground.lambdatest.io/",
            "Click on any product tile",
            "Verify the product detail page loads with name and price",
        ]
    if "search" in lowered:
        return [
            "Navigate to https://ecommerce-playground.lambdatest.io/",
            "Enter a search term in the search bar",
            "Verify relevant results are displayed",
        ]
    if "without logging in" in lowered or "highlight" in lowered:
        return [
            "Navigate to https://ecommerce-playground.lambdatest.io/ without logging in",
            "Verify featured products or carousel is visible",
        ]
    return [
        "Navigate to https://ecommerce-playground.lambdatest.io/",
        "Perform the action described in the acceptance criterion",
        "Verify the expected outcome is achieved",
    ]


def _fallback_expected(description):
    return description.capitalize()


def main():
    args = parse_args()
    print_stage_header("2", "MANAGE_SCENARIOS", "Sync scenarios.json with analyzed requirements")
    requirements = load_json(args.requirements, [])
    scenarios = load_json(args.scenarios, [])
    existing_by_requirement = {scenario["requirement_id"]: scenario for scenario in scenarios}
    today = datetime.now(timezone.utc).date().isoformat()

    updated = []
    counts = {"active": 0, "updated": 0, "new": 0, "deprecated": 0}
    active_requirement_ids = set()

    for index, requirement in enumerate(requirements, start=1):
        active_requirement_ids.add(requirement["id"])
        title, steps, expected = title_and_steps(requirement)
        scenario = existing_by_requirement.get(requirement["id"])
        status = "new"
        if scenario:
            status = "active" if scenario.get("source_description") == requirement["description"] else "updated"
        else:
            scenario = {}

        record = {
            "id": scenario.get("id", f"SC-{index:03d}"),
            "requirement_id": requirement["id"],
            "title": title,
            "steps": steps,
            "expected_result": expected,
            "status": status,
            "kane_objective": _get_kane_objective(requirement["description"]),
            # Preserve the existing kane_url when updating an existing scenario so
            # scenario-specific starting URLs (e.g. category pages) are not reset
            # to the homepage on every requirements change.
            "kane_url": scenario.get("kane_url", requirement["url"]) if scenario else requirement["url"],
            "kane_last_status": requirement.get("kane_status", "pending"),
            "test_case_id": scenario.get("test_case_id", f"TC-{index:03d}"),
            "last_verified": today,
            "source_description": requirement["description"],
        }

        if requirement.get("kane_status") == "failed":
            record["kane_failure_reason"] = requirement.get("kane_summary", "")

        updated.append(record)
        counts[status] += 1

    for scenario in scenarios:
        if scenario["requirement_id"] in active_requirement_ids:
            continue
        deprecated = dict(scenario)
        deprecated["status"] = "deprecated"
        updated.append(deprecated)
        counts["deprecated"] += 1

    output = Path(args.scenarios)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")

    print_stage_result("2", "MANAGE_SCENARIOS", {
        "Active":     counts["active"],
        "Updated":    counts["updated"],
        "New":        counts["new"],
        "Deprecated": counts["deprecated"],
        "Total":      len(updated),
        "Output":     args.scenarios,
    })


if __name__ == "__main__":
    main()
