"""
Generates Kane AI TestMD files (_test.md) from plain-English requirements.
Each requirement becomes a structured _test.md with YAML frontmatter,
H2-delimited steps, code_export enabled, and {{app_url}} variable.
Also generates helper files and .kane-config.json for TMS wiring.
"""
from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path

from repo_analyzer import RepoProfile


# ---------------------------------------------------------------------------
# Requirement → TestMD step decomposition
# ---------------------------------------------------------------------------

_STEP_SPLIT_PHRASES = [
    r"\s+—\s+",           # em dash separator (used in kane objectives)
    r"\s+-\s+",           # hyphen separator
    r"\.\s+(?=[A-Z])",    # sentence boundary
    r";\s+",              # semicolon
]

_BANNER_STEPS = [
    ("Open Application",
     "Open {{app_url}} and wait for the full page to load. Confirm the main navigation is visible."),
    ("Verify Banner",
     "Look near the top of the page — above or inside the header navigation — for a promotional "
     "banner, announcement bar, or highlighted strip. Verify the banner text contains the expected "
     "promotional message. If no banner is visible after the page loads, fail this step and describe "
     "what the header area currently shows."),
]

_SEARCH_STEPS = [
    ("Open Application", "Open {{app_url}} and wait for the search box to be visible."),
    ("Perform Search", "Type the search term into the search box and submit the query."),
    ("Verify Results", "Verify that relevant search results are displayed on the page."),
]

_LOGIN_STEPS = [
    ("Open Login Page", "Navigate to {{app_url}}/login or the login link and wait for the form."),
    ("Enter Credentials", "Enter the username and password into the respective fields."),
    ("Submit and Verify", "Click the login button and verify successful authentication."),
]

_GENERIC_STEPS = [
    ("Open Application", "Open {{app_url}} and wait for the page to fully load."),
    ("Execute Action", "Perform the required action as described in the test objective."),
    ("Verify Outcome", "Verify the expected outcome is visible on the page."),
]


def _slug(text: str) -> str:
    """Convert a requirement string to a safe filename slug."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "_", s)
    return s[:60].strip("_")


def _infer_steps(requirement: str) -> list[tuple[str, str]]:
    """Infer test steps from the requirement text."""
    req_lower = requirement.lower()
    if any(w in req_lower for w in ("banner", "promotional", "announcement", "sale", "offer")):
        steps = list(_BANNER_STEPS)
        # Inject the actual requirement text into the verify step
        expected = re.search(r'"([^"]+)"', requirement)
        if expected:
            steps[1] = (steps[1][0],
                        steps[1][1].replace("the expected promotional message",
                                            f'"{expected.group(1)}"'))
        return steps
    if any(w in req_lower for w in ("search", "find", "query")):
        return list(_SEARCH_STEPS)
    if any(w in req_lower for w in ("login", "sign in", "log in", "authenticate")):
        return list(_LOGIN_STEPS)
    return list(_GENERIC_STEPS)


def _build_frontmatter(app_url: str, slug: str) -> str:
    return textwrap.dedent(f"""\
        ---
        mode: testing
        headless: true
        max_steps: 30
        timeout: 120
        code_export: true
        code_language: typescript
        variables:
          app_url:
            value: "{app_url}"
        ---
    """)


def _build_testmd_content(title: str, steps: list[tuple[str, str]], app_url: str, slug: str) -> str:
    lines = [_build_frontmatter(app_url, slug), "", f"# {title}", ""]
    for step_title, step_body in steps:
        lines.append(f"## {step_title}")
        lines.append(step_body)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper file templates
# ---------------------------------------------------------------------------

_NAVIGATE_HOME_HELPER = """\
Open {{app_url}} and wait for the navigation header to be visible.
"""

_KANE_CONFIG_TEMPLATE = """\
{{
  "project_id": "{project_id}",
  "folder_id": "{folder_id}"
}}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(
    requirements: list[str],
    repo_profile: RepoProfile,
    workspace_dir: str,
    project_id: str = "",
    folder_id: str = "",
) -> list[str]:
    """
    Generate TestMD files for a list of requirements.

    Args:
        requirements: List of plain-English requirement strings.
        repo_profile: RepoProfile from repo_analyzer.analyze().
        workspace_dir: Local path to cloned target repo.
        project_id: Kane AI / TestMu project ID for TMS wiring.
        folder_id: Kane AI / TestMu folder ID for TMS wiring.

    Returns:
        List of generated _test.md file paths (relative to workspace_dir).
    """
    testmd_dir = Path(workspace_dir) / "kane" / "testmd"
    helpers_dir = Path(workspace_dir) / "kane" / "helpers"
    testmd_dir.mkdir(parents=True, exist_ok=True)
    helpers_dir.mkdir(parents=True, exist_ok=True)

    app_url = repo_profile.target_url or repo_profile.app_url_local
    generated: list[str] = []

    # Write .kane-config.json for TMS wiring
    if project_id or folder_id:
        config_path = testmd_dir / ".kane-config.json"
        config_path.write_text(
            _KANE_CONFIG_TEMPLATE.format(
                project_id=project_id or "",
                folder_id=folder_id or "",
            ),
            encoding="utf-8",
        )
        print(f"[testmd_generator] wrote .kane-config.json (project={project_id} folder={folder_id})")

    # Write navigate_home helper
    helper_path = helpers_dir / "navigate_home.md"
    if not helper_path.exists():
        helper_path.write_text(_NAVIGATE_HOME_HELPER, encoding="utf-8")
        print(f"[testmd_generator] wrote {helper_path}")

    # Generate one _test.md per requirement
    for req in requirements:
        slug = _slug(req)
        filename = f"{slug}_test.md"
        dest = testmd_dir / filename

        # Build a clean title from the requirement
        title = req.strip().rstrip(".")
        if len(title) > 80:
            title = title[:77] + "..."

        steps = _infer_steps(req)
        content = _build_testmd_content(title, steps, app_url, slug)

        dest.write_text(content, encoding="utf-8")
        rel_path = str(dest.relative_to(workspace_dir)).replace("\\", "/")
        generated.append(rel_path)
        print(f"[testmd_generator] wrote {rel_path} ({len(steps)} steps)")

    return generated


def generate_single(
    requirement: str,
    app_url: str,
    output_dir: str,
    project_id: str = "",
    folder_id: str = "",
) -> str:
    """
    Generate a single _test.md for a requirement without needing a full RepoProfile.
    Useful for the existing analyze_requirements.py pipeline.

    Returns the path to the written file.
    """
    slug = _slug(requirement)
    filename = f"{slug}_test.md"
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    title = requirement.strip().rstrip(".")
    if len(title) > 80:
        title = title[:77] + "..."

    steps = _infer_steps(requirement)
    content = _build_testmd_content(title, steps, app_url, slug)
    dest = out_dir / filename
    dest.write_text(content, encoding="utf-8")
    return str(dest)


if __name__ == "__main__":
    import sys
    req = sys.argv[1] if len(sys.argv) > 1 else \
        "The application top banner should display a Memorial Day Sale banner."
    out = generate_single(req, "http://localhost:3000", "kane/testmd")
    print(f"Generated: {out}")
    print(Path(out).read_text())
