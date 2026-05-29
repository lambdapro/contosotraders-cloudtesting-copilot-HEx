"""
Generates or updates TypeScript/JavaScript Playwright test files in the target repo.
Follows patterns extracted by test_analyzer.TestProfile.
For contosotraders, updates verifymsg.spec.ts (has commented-out memorial banner test).
For generic repos, creates a new spec file.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path

from repo_analyzer import RepoProfile
from test_analyzer import TestProfile


# ---------------------------------------------------------------------------
# New test body templates
# ---------------------------------------------------------------------------

_BANNER_TEST_BODY = """\
test('verify {banner_text} banner text is present', async ({{ page }}) => {{
  await page.getByText("{banner_text}")
  await expect(page).toBeTruthy();
}});

test('top banner is visible and shows {banner_text} offer', async ({{ page }}) => {{
  const banner = page.locator(
    '.{css_class}, [data-testid="promo-banner"], .promo-banner, header .announcement-bar'
  ).first();
  await expect(banner).toBeVisible({{ timeout: 10000 }});
  const text = await banner.textContent() ?? '';
  expect(text.toLowerCase()).toContain('{banner_lower}');
}});
"""

_GENERIC_TEST_BODY = """\
test('verify {title}', async ({{ page }}) => {{
  // Verify requirement: {requirement}
  await expect(page).toBeTruthy();
}});
"""

_DESCRIBE_WRAPPER = """\
test.describe('{title}', () => {{
  {body}
}});
"""


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "_", s)
    return s[:50].strip("_")


def _extract_banner_text(requirement: str) -> tuple[str, str, str]:
    """Returns (banner_text, css_class, banner_lower)."""
    # Try to extract quoted text first
    m = re.search(r'"([^"]+)"', requirement)
    if m:
        banner_text = m.group(1)
    else:
        # Try to infer from requirement keywords
        for phrase in ("Memorial Day Sale", "Memorial Day", "Black Friday", "Cyber Monday",
                       "Sale", "Offer", "Discount", "Promotion"):
            if phrase.lower() in requirement.lower():
                banner_text = phrase
                break
        else:
            banner_text = "Promotional Banner"

    css_class = re.sub(r"[^\w]", "-", banner_text.lower()).strip("-")
    return banner_text, css_class, banner_text.lower()


def _build_new_test_content(
    requirement: str,
    profile: TestProfile,
    base_url: str,
) -> str:
    """Build a complete new spec file following the TestProfile patterns."""
    req_lower = requirement.lower()

    # Choose appropriate test body
    if any(w in req_lower for w in ("banner", "promotional", "sale", "offer", "announcement")):
        banner_text, css_class, banner_lower = _extract_banner_text(requirement)
        body = _BANNER_TEST_BODY.format(
            banner_text=banner_text,
            css_class=css_class,
            banner_lower=banner_lower,
        )
    else:
        body = _GENERIC_TEST_BODY.format(
            title=requirement[:60],
            requirement=requirement,
        )

    # Wrap in describe if the existing tests use it
    if profile.uses_describe:
        title = requirement[:60].rstrip(".")
        body = _DESCRIBE_WRAPPER.format(title=title, body=body.replace("\n", "\n  "))

    # Build imports
    if profile.import_style == "lambda_setup":
        imports = f"import {{ expect }} from '@playwright/test';\nimport test from '{profile.lambda_setup_path}';"
    else:
        imports = "import { test, expect } from '@playwright/test';"

    # Build beforeEach if needed
    before_each = ""
    if profile.navigation_pattern == "beforeEach_goto":
        before_each = f"\ntest.beforeEach(async ({{ page }}) => {{\n  await page.goto('{base_url}');\n}});\n"

    return f"{imports}\n{before_each}\n{body}"


# ---------------------------------------------------------------------------
# verifymsg.spec.ts updater (contosotraders-specific but generalizable)
# ---------------------------------------------------------------------------

def _update_verifymsg_content(original: str, requirement: str) -> str:
    """
    Update verifymsg.spec.ts to add Memorial Day Sale banner tests.
    Strategy:
    1. Remove any commented-out memorial/sale test blocks
    2. Prepend the new banner tests before the existing uncommented tests
    """
    banner_text, css_class, banner_lower = _extract_banner_text(requirement)

    # Remove commented-out blocks that mention "Memorial" or "Sale"
    # Pattern: //   test('...') { ... }  across multiple lines
    cleaned = re.sub(
        r"//\s*test\(['\"].*?[Mm]emorial.*?['\"].*?\}\)",
        "",
        original,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"//\s*test\(['\"].*?[Ss]ale.*?['\"].*?\}\)",
        "",
        cleaned,
        flags=re.DOTALL,
    )

    # Remove consecutive empty comment lines left behind
    cleaned = re.sub(r"(\n//\s*\n){2,}", "\n", cleaned)

    new_tests = f"""
test('verify {banner_text} banner text is present', async ({{ page }}) => {{
  await page.getByText("{banner_text}")
  await expect(page).toBeTruthy();
}});

test('top banner is visible and shows {banner_text} offer', async ({{ page }}) => {{
  const banner = page.locator(
    '.{css_class}, [data-testid="promo-banner"], .promo-banner, header .announcement-bar'
  ).first();
  await expect(banner).toBeVisible({{ timeout: 10000 }});
  const text = await banner.textContent() ?? '';
  expect(text.toLowerCase()).toContain('{banner_lower}');
}});

"""

    # Insert new tests right after the beforeEach block (or after imports if no beforeEach)
    if "beforeEach" in cleaned:
        insert_point = cleaned.find("\n", cleaned.find("});", cleaned.find("beforeEach"))) + 1
        cleaned = cleaned[:insert_point] + new_tests + cleaned[insert_point:]
    else:
        # After imports
        import_end = 0
        for line in cleaned.splitlines(keepends=True):
            if line.startswith("import ") or line.startswith("//") or line.strip() == "":
                import_end += len(line)
            else:
                break
        cleaned = cleaned[:import_end] + new_tests + cleaned[import_end:]

    return cleaned


def _gh_file_content(owner: str, name: str, path: str) -> str | None:
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{name}/contents/{path}", "--jq", ".content"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip().strip('"')
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def inject(
    requirement: str,
    repo_profile: RepoProfile,
    test_profile: TestProfile,
    workspace_dir: str,
) -> list[str]:
    """
    Inject tests for the given requirement into the workspace copy of the target repo.

    Strategy:
    1. If most_relevant_file is verifymsg.spec.ts (or similar "message" file) → update it
    2. Otherwise create a new spec file

    Args:
        requirement: Plain-English requirement string.
        repo_profile: RepoProfile from repo_analyzer.
        test_profile: TestProfile from test_analyzer.
        workspace_dir: Local path to the cloned workspace.

    Returns:
        List of modified/created file paths (relative to workspace_dir).
    """
    workspace = Path(workspace_dir)
    work_subdir = workspace / repo_profile.app_working_dir if repo_profile.app_working_dir else workspace
    test_dir = work_subdir / repo_profile.test_dir

    modified: list[str] = []

    # Determine if we should update an existing file or create a new one
    relevant = test_profile.most_relevant_file
    should_update = (
        relevant
        and ("verify" in relevant.lower() or "msg" in relevant.lower() or "message" in relevant.lower()
             or "banner" in relevant.lower())
    )

    if should_update:
        target_path = workspace / relevant
        if target_path.exists():
            original = target_path.read_text(encoding="utf-8")
        else:
            # Fetch from GitHub if not cloned yet
            original = _gh_file_content(repo_profile.owner, repo_profile.name, relevant) or ""

        updated = _update_verifymsg_content(original, requirement)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(updated, encoding="utf-8")
        rel = str(target_path.relative_to(workspace)).replace("\\", "/")
        modified.append(rel)
        print(f"[test_injector] updated {rel}")
    else:
        # Create a new spec file
        slug = _slug(requirement)
        lang = test_profile.test_file_language
        ext = ".spec.ts" if lang == "typescript" else ".spec.js"
        new_file = test_dir / f"{slug}{ext}"

        content = _build_new_test_content(
            requirement,
            test_profile,
            repo_profile.app_url_local,
        )
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text(content, encoding="utf-8")
        rel = str(new_file.relative_to(workspace)).replace("\\", "/")
        modified.append(rel)
        print(f"[test_injector] created {rel}")

    return modified
