"""
Generates hyperexecute-agentic.yaml for a target repo using matrix mode.
Chrome, Firefox, and Edge each run in parallel on their own HyperExecute VM.
Does NOT modify the existing hyperExecute.yaml.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

from repo_analyzer import RepoProfile


_DEFAULT_LT_BROWSERS = [
    "chrome:latest:Windows10@lambdatest",
    "MicrosoftEdge:latest:Windows10@lambdatest",
    "pw-firefox:latest@lambdatest",
]

_WAIT_CMD = {
    "win": "timeout 20",
    "linux": "sleep 20",
    "mac": "sleep 20",
}


def _install_browsers_cmd(runson: str) -> str:
    """Return the playwright install command appropriate for the OS."""
    if runson == "win":
        return "npx playwright install --with-deps chromium firefox msedge"
    return "npx playwright install --with-deps chromium firefox"


def _build_yaml(
    repo_profile: RepoProfile,
    test_files: list[str],
    he_config_name: str = "hyperexecute-agentic.yaml",
) -> str:
    """
    Build the hyperexecute-agentic.yaml content.

    Args:
        repo_profile: RepoProfile for the target repo.
        test_files: List of test file paths to include (relative to app_working_dir).
        he_config_name: Output filename (informational only).

    Returns:
        YAML content as a string.
    """
    runson = repo_profile.he_runson or "win"
    wait_cmd = _WAIT_CMD.get(runson, "sleep 20")
    install_browsers = _install_browsers_cmd(runson)
    install_cmd = repo_profile.install_cmd  # e.g. "npm ci"

    # Use LambdaTest project names from playwright.config.ts if they exist;
    # otherwise fall back to the three standard browsers.
    lt_projects = [p for p in repo_profile.playwright_projects if "@lambdatest" in p]
    if not lt_projects:
        lt_projects = _DEFAULT_LT_BROWSERS

    # Build browser matrix YAML list
    browser_lines = "\n".join(f"    - {b}" for b in lt_projects)

    # Build test discovery:
    # If single file → static echo; if multiple → static list with newlines
    if len(test_files) == 1:
        discovery_cmd = f'echo "{test_files[0]}"'
        has_test_file_matrix = False
    else:
        # Use a second matrix dimension for test files
        has_test_file_matrix = True
        discovery_cmd = ""  # unused in matrix mode

    # Build testRunnerCommand
    if has_test_file_matrix:
        runner_cmd = 'npx playwright test $testFile --project="$browser" --reporter=junit,html'
    else:
        runner_cmd = f'npx playwright test {test_files[0] if test_files else "tests/"} --project="$browser" --reporter=junit,html'

    # concurrency = browsers × test_files
    n_browsers = len(lt_projects)
    n_files = len(test_files) if has_test_file_matrix else 1
    concurrency = n_browsers * n_files

    # Static discovery section (matrix mode doesn't use testDiscovery for the matrix axis)
    if not has_test_file_matrix:
        discovery_section = textwrap.dedent(f"""\
            testDiscovery:
              type: raw
              mode: static
              command: {discovery_cmd}
        """)
        matrix_section = textwrap.dedent(f"""\
            matrix:
              browser:
            {browser_lines}
        """)
    else:
        file_lines = "\n".join(f"    - {f}" for f in test_files)
        discovery_section = ""
        matrix_section = textwrap.dedent(f"""\
            matrix:
              browser:
            {browser_lines}
              testFile:
            {file_lines}
        """)

    yaml_content = textwrap.dedent(f"""\
        version: 0.1
        globalTimeout: 150
        testSuiteTimeout: 90
        testSuiteStep: 90
        runson: {runson}
        retryOnFailure: true
        maxRetries: 1
        autosplit: false
        concurrency: {concurrency}
        tunnel: true

        {matrix_section.rstrip()}

        pre:
          - {install_cmd}
          - {install_browsers}
          - npm start &
          - {wait_cmd}

        cacheDirectories:
          - node_modules
        cacheKey: '{{% checksum "package-lock.json" %}}'

        env:
          LT_USERNAME: ${{LT_USERNAME}}
          LT_ACCESS_KEY: ${{LT_ACCESS_KEY}}

        {discovery_section.rstrip() + chr(10) if discovery_section else ""}testRunnerCommand: {runner_cmd}

        mergeArtifacts: true
        uploadArtefacts:
          - name: PlaywrightReports
            path:
              - playwright-report/
              - test-results/

        report: true
        partialReports:
          type: json
          location: playwright-report/
          frameworkName: playwright
    """)

    return yaml_content


def build(
    repo_profile: RepoProfile,
    test_files: list[str],
    workspace_dir: str,
    output_name: str = "hyperexecute-agentic.yaml",
) -> str:
    """
    Write hyperexecute-agentic.yaml to the app working directory in the workspace.

    Args:
        repo_profile: RepoProfile for the target repo.
        test_files: Test file paths to include (relative to app_working_dir).
        workspace_dir: Local path to the cloned workspace.
        output_name: Filename of the generated HE config.

    Returns:
        Relative path (from workspace_dir) to the written file.
    """
    workspace = Path(workspace_dir)
    if repo_profile.app_working_dir:
        dest_dir = workspace / repo_profile.app_working_dir
    else:
        dest_dir = workspace
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / output_name
    content = _build_yaml(repo_profile, test_files, output_name)
    dest.write_text(content, encoding="utf-8")
    rel = str(dest.relative_to(workspace)).replace("\\", "/")
    print(f"[hyperexecute_builder] wrote {rel} ({len(test_files)} test(s), "
          f"{len(repo_profile.playwright_projects or _DEFAULT_LT_BROWSERS)} browsers in matrix)")
    return rel


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from repo_analyzer import RepoProfile
    profile = RepoProfile(
        repo_url="https://github.com/lambdapro/contosotraders-cloudtesting-copilot-HEx",
        owner="lambdapro", name="contosotraders-cloudtesting-copilot-HEx",
        default_branch="main", framework="react", package_manager="npm",
        install_cmd="npm ci", start_cmd="npm start", build_cmd="npm run build",
        app_port=3000, app_working_dir="src/ContosoTraders.Ui.Website",
        test_framework="playwright", test_dir="tests",
        playwright_config="src/ContosoTraders.Ui.Website/playwright.config.ts",
        he_config="src/ContosoTraders.Ui.Website/hyperExecute.yaml",
        lt_integration=True,
        existing_test_files=["src/ContosoTraders.Ui.Website/tests/verifymsg.spec.ts"],
        app_url_local="http://localhost:3000",
        kane_mode="local", node_version="18", he_runson="win",
        playwright_projects=[
            "chrome:latest:Windows10@lambdatest",
            "MicrosoftEdge:latest:Windows10@lambdatest",
            "pw-firefox:latest@lambdatest",
        ],
    )
    yaml = _build_yaml(profile, ["tests/verifymsg.spec.ts"])
    print(yaml)
