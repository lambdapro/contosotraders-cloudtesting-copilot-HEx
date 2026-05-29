"""
Main orchestrator for the Repo Upgrade + Autonomous Validation Skill.

Creates a fully self-contained local workspace at agentic-stlc/ and injects
the complete Agentic STLC pipeline into any GitHub repo's .agentic/ folder.

LOCAL WORKSPACE STRUCTURE (created before any clone):
  agentic-stlc/
  ├── {repo-name}/              <- cloned target repo
  │   ├── .agentic/             <- complete pipeline (CI scripts + config + state)
  │   └── .github/workflows/   <- injected agentic-stlc.yml
  ├── ci/                       <- ALL pipeline + skill scripts (local copy)
  ├── templates/
  │   ├── kane/testmd/          <- base TestMD template
  │   ├── hyperexecute/         <- base HE config template
  │   └── workflows/            <- GHA workflow template placeholder
  ├── requirements.txt
  ├── pytest.ini
  ├── agentic-stlc.config.yaml
  └── tests/playwright/conftest.py

.AGENTIC/ FOLDER (injected into the target repo):
  .agentic/ci/                  <- All 25 pipeline Python scripts
  .agentic/requirements.txt     <- Python deps (mcp, httpx, playwright, pytest...)
  .agentic/pytest.ini
  .agentic/hyperexecute.yaml    <- Adapted for target app URL
  .agentic/agentic-stlc.config.yaml <- Adapted for target repo
  .agentic/requirements/search.txt  <- The business requirement (input to pipeline)
  .agentic/scenarios/scenarios.json <- Empty; populated by pipeline Stage 2
  .agentic/tests/playwright/conftest.py <- LambdaTest Playwright fixture

GitHub Actions runs .agentic/ci/analyze_requirements.py (Stage 1 / Kane AI)
and .agentic/ci/agent.py (Stages 2-7 / Orchestrator) -all processing in CI.
Execution mirrors: lambdapro/agentic-stlc-kane-hyperexecute (3-job pipeline).

Modes:
  --mode analyze  : Read-only repo analysis, prints RepoProfile (no branch created)
  --mode inject   : Bootstrap workspace + clone + inject pipeline + push (no GHA trigger)
  --mode run      : Full pipeline -bootstrap + inject + trigger + watch + collect (default)

Usage:
    python ci/repo_upgrade_orchestrator.py \\
        --repo-url https://github.com/lambdapro/contosotraders-cloudtesting-copilot-HEx \\
        --requirement "The application top banner should display a Memorial Day Sale banner." \\
        --target-url https://contoso-traders.lambdatest.io \\
        --branch feature/agentic-memorial-day-banner
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Ensure ci/ is on sys.path
_CI_DIR = Path(__file__).parent
_REPO_ROOT = _CI_DIR.parent
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

import repo_analyzer
import gha_injector

# ---------------------------------------------------------------------------
# Pipeline CI scripts copied from orchestration repo -> .agentic/ci/
# Skill-specific scripts (repo_upgrade_*, gha_injector, etc.) are excluded.
# ---------------------------------------------------------------------------

_PIPELINE_CI_FILES = [
    "ci/analyze_requirements.py",
    "ci/agent.py",
    "ci/manage_scenarios.py",
    "ci/select_tests.py",
    "ci/build_traceability.py",
    "ci/release_recommendation.py",
    "ci/write_github_summary.py",
    "ci/analyze_hyperexecute_failures.py",
    "ci/fetch_api_details.py",
    "ci/run_pytest_node.py",
    "ci/stage_utils.py",
    "ci/pipeline_metrics.py",
    "ci/notify_agent.py",
    "ci/fetch_rca.py",
    "ci/quality_gates.py",
    "ci/scenario_confidence.py",
    "ci/coverage_analysis.py",
    "ci/impact_analysis.py",
    "ci/failure_intelligence.py",
    "ci/self_healing.py",
    "ci/normalize_artifacts.py",
    "ci/collect_kane_exports.py",
    "ci/rca_enrichment.py",
    "ci/validate_report.py",
    "ci/rca_parser.py",
]

# Repo-upgrade skill scripts -local workspace only (not needed in .agentic/ci/)
_SKILL_CI_FILES = [
    "ci/repo_analyzer.py",
    "ci/testmd_generator.py",
    "ci/test_analyzer.py",
    "ci/test_injector.py",
    "ci/hyperexecute_builder.py",
    "ci/gha_injector.py",
    "ci/repo_upgrade_orchestrator.py",
    "ci/conversational_orchestrator.py",
]

# All CI scripts for the local workspace (pipeline + skill scripts)
_ALL_WORKSPACE_CI_FILES = _PIPELINE_CI_FILES + _SKILL_CI_FILES

# Files copied from repo root -> .agentic/  (and also to workspace root)
_PIPELINE_ROOT_FILES = [
    ("requirements.txt",                 "requirements.txt"),
    ("pytest.ini",                       "pytest.ini"),
    ("tests/playwright/conftest.py",     "tests/playwright/conftest.py"),
]

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    config_path = _REPO_ROOT / "agentic-stlc.config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        return {}


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:50].strip("-")


# ---------------------------------------------------------------------------
# Stage A: Bootstrap the local orchestration workspace
# ---------------------------------------------------------------------------

def bootstrap_local_workspace(
    workspace_root: Path,
    profile: repo_analyzer.RepoProfile,
    config: dict,
) -> None:
    """
    Create the self-contained local orchestration runtime at workspace_root/.

    After this call the workspace contains everything needed to run the
    orchestrator locally AND serves as the source for .agentic/ injection:

      workspace_root/
      ├── ci/                    <- ALL pipeline + skill scripts (local copy)
      ├── templates/
      │   ├── kane/testmd/       <- base TestMD template
      │   ├── hyperexecute/      <- base HE config template
      │   └── workflows/         <- placeholder (workflow generated at inject time)
      ├── requirements.txt
      ├── pytest.ini
      ├── agentic-stlc.config.yaml
      └── tests/playwright/conftest.py

    The cloned target repo is placed as a sibling: workspace_root/{repo_name}/
    """
    workspace_root.mkdir(parents=True, exist_ok=True)
    print(f"\n[Stage A] Bootstrapping local workspace -> {workspace_root}")

    total = 0

    # 1. Copy ALL CI scripts (pipeline + skill scripts)
    ci_dest = workspace_root / "ci"
    ci_dest.mkdir(parents=True, exist_ok=True)
    for rel_path in _ALL_WORKSPACE_CI_FILES:
        src = _REPO_ROOT / rel_path
        if not src.exists():
            print(f"  [warn] source not found: {rel_path}")
            continue
        dest = ci_dest / src.name
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        total += 1
    print(f"  ci/        {total} scripts (pipeline + skill)")

    # 2. Copy support files (requirements.txt, pytest.ini, conftest.py)
    support = 0
    for src_rel, dest_rel in _PIPELINE_ROOT_FILES:
        src = _REPO_ROOT / src_rel
        if not src.exists():
            print(f"  [warn] source not found: {src_rel}")
            continue
        dest = workspace_root / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        support += 1
    print(f"  support    {support} files (requirements.txt, pytest.ini, conftest.py)")

    # 3. Copy template files
    _write_workspace_templates(workspace_root, profile)

    # 4. Write workspace-level adapted config
    kane_cfg = config.get("kaneai", {})
    project_id = os.getenv("KANE_PROJECT_ID", "") or kane_cfg.get("project_id", "")
    folder_id = os.getenv("KANE_FOLDER_ID", "") or kane_cfg.get("folder_id", "")
    target_url = profile.target_url or profile.app_url_local or ""
    ws_config = _build_workspace_config(profile, project_id, folder_id, target_url)
    (workspace_root / "agentic-stlc.config.yaml").write_text(ws_config, encoding="utf-8")

    # 5. Initialize empty state dirs (mirroring .agentic/ structure for local runs)
    for rel in ["requirements", "scenarios", "reports", "kane", "tests/playwright"]:
        (workspace_root / rel).mkdir(parents=True, exist_ok=True)

    print(f"\n  Workspace ready: {workspace_root.resolve()}")
    print(f"    {workspace_root.name}/")
    print(f"    +-- ci/                      ({total} scripts)")
    print(f"    +-- templates/kane/testmd/   (base TestMD template)")
    print(f"    +-- templates/hyperexecute/  (base HE config)")
    print(f"    +-- requirements.txt + pytest.ini")
    print(f"    +-- agentic-stlc.config.yaml (adapted for {profile.name})")


def _write_workspace_templates(workspace_root: Path, profile: repo_analyzer.RepoProfile) -> None:
    """Write template files into workspace_root/templates/."""
    target_url = profile.target_url or profile.app_url_local or "http://localhost:3000"

    # Base Kane TestMD template
    testmd_dir = workspace_root / "templates" / "kane" / "testmd"
    testmd_dir.mkdir(parents=True, exist_ok=True)
    base_testmd = f"""\
---
mode: testing
headless: true
max_steps: 30
timeout: 120
code_export: true
code_language: typescript
variables:
  app_url:
    value: "{target_url}"
---

# Requirement Verification

## Open Application
Open {{{{app_url}}}} and wait for the full page to load. Confirm the main navigation is visible.

## Verify Feature
Describe the feature verification steps in plain English. Be specific about what element
to locate and what text, state, or condition to assert.
If the expected element is not visible after page load, fail this step and describe
what is currently shown.
"""
    (testmd_dir / "base_test.md").write_text(base_testmd, encoding="utf-8")

    # Also write a helper template
    helper_dir = workspace_root / "templates" / "kane" / "helpers"
    helper_dir.mkdir(parents=True, exist_ok=True)
    (helper_dir / "navigate_home.md").write_text(
        f"Open {{{{app_url}}}} and wait for the navigation header to be visible.\n",
        encoding="utf-8",
    )

    # Base HyperExecute config template (copy from orchestration repo)
    he_src = _REPO_ROOT / "hyperexecute.yaml"
    he_template_dir = workspace_root / "templates" / "hyperexecute"
    he_template_dir.mkdir(parents=True, exist_ok=True)
    if he_src.exists():
        (he_template_dir / "hyperexecute.yaml").write_text(
            he_src.read_text(encoding="utf-8"), encoding="utf-8"
        )

    # Workflows placeholder -actual content generated by gha_injector at inject time
    workflows_dir = workspace_root / "templates" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / ".gitkeep").write_text("", encoding="utf-8")

    print(f"  templates/ kane/testmd, hyperexecute, workflows")


def _build_workspace_config(
    profile: repo_analyzer.RepoProfile,
    project_id: str,
    folder_id: str,
    target_url: str,
) -> str:
    return f"""\
# Agentic STLC Platform -Workspace Configuration
# Auto-generated by Agentic STLC repo-upgrade skill
# Target repository: {profile.repo_url}

version: "1.0"

project:
  name: "{profile.name.lower()}-stlc"
  description: "Agentic STLC pipeline for {profile.name}"
  repository: "{profile.repo_url}"
  branch: "{profile.default_branch}"

requirements:
  format: "acceptance_criteria"
  paths:
    - "requirements/search.txt"
  output_path: "requirements/analyzed_requirements.json"
  encoding: "utf-8"

scenarios:
  path: "scenarios/scenarios.json"
  id_prefix: "SC"
  id_start: 1

framework:
  type: "playwright"
  language: "python"
  test_dir: "tests/playwright"
  test_file: "tests/playwright/test_powerapps.py"

target:
  url: "{target_url}"
  environment: "staging"

execution:
  provider: "hyperexecute"
  mode: "incremental"
  concurrency: 5
  timeout_seconds: 90
  retries: 1
  browsers:
    - chrome
  platforms:
    - windows10

hyperexecute:
  config_file: "hyperexecute.yaml"
  cli_path: "./hyperexecute"
  project: "{profile.name.lower()}-stlc"
  region: "us"

kaneai:
  enabled: true
  parallel_workers: 10
  timeout_seconds: 120
  project_id: "{project_id}"
  folder_id: "{folder_id}"
  use_testmd: true
  testmd_output_dir: "kane/testmd"

reporting:
  output_dir: "reports"
  formats:
    - json
    - markdown
  github_summary: true
  artifacts:
    - "reports/"
    - "scenarios/scenarios.json"
    - "tests/playwright/test_powerapps.py"

quality_gates:
  min_coverage_pct: 50
  min_pass_rate: 75
  max_flaky: 5
  require_critical_coverage: true
  max_high_risk_uncovered: 999
  min_he_pct: 0
  confidence:
    enabled: true
    gate_severity: "WARNING"

repo_upgrade:
  workspace_dir: "agentic-stlc"
  branch_prefix: "feature/agentic-"
  he_config_name: "hyperexecute-agentic.yaml"
  gha_workflow_name: "agentic-stlc.yml"
  default_node_version: "18"
  wait_on_timeout_ms: 60000
"""


# ---------------------------------------------------------------------------
# Git + GitHub helpers
# ---------------------------------------------------------------------------

def _clean_env() -> dict:
    """Return os.environ without GH_TOKEN so gh uses keyring auth."""
    env = {**os.environ}
    env.pop("GH_TOKEN", None)
    return env


def _run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"[orchestrator] $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=False, env=_clean_env())


def _run_capture(cmd: list[str], cwd: str | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=_clean_env())
    return result.stdout.strip()


def clone_repo(repo_url: str, workspace_dir: str) -> str:
    """Clone the target repo into workspace_dir/{repo_name}. Returns the clone path."""
    owner_name = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?$", repo_url.rstrip("/"))
    if not owner_name:
        raise ValueError(f"Cannot parse repo name from {repo_url}")
    repo_name = owner_name.group(1).split("/")[1]
    clone_path = Path(workspace_dir) / repo_name
    if clone_path.exists():
        print(f"[orchestrator] workspace already exists at {clone_path}, pulling latest")
        _run(["git", "fetch", "origin"], cwd=str(clone_path))
    else:
        clone_path.parent.mkdir(parents=True, exist_ok=True)
        # core.longpaths=true avoids Windows 260-char path failures on repos
        # that have long log or artifact filenames checked in.
        _run(["git", "clone", "-c", "core.longpaths=true", repo_url, str(clone_path)])
    return str(clone_path)


def create_branch(workspace_path: str, branch: str) -> None:
    _run(["git", "checkout", "-B", branch], cwd=workspace_path)


def commit_and_push(workspace_path: str, branch: str, message: str) -> str:
    _run(["git", "add", "."], cwd=workspace_path)
    status = _run_capture(["git", "status", "--porcelain"], cwd=workspace_path)
    if not status:
        print("[orchestrator] nothing to commit -files already up to date")
        return _run_capture(["git", "rev-parse", "HEAD"], cwd=workspace_path)
    _run(["git", "commit", "-m", message], cwd=workspace_path)
    _run(["git", "push", "origin", branch, "--force-with-lease"], cwd=workspace_path)
    sha = _run_capture(["git", "rev-parse", "HEAD"], cwd=workspace_path)
    print(f"[orchestrator] pushed {branch} -> {sha[:8]}")
    return sha


def trigger_workflow(owner: str, name: str, branch: str, workflow: str = "agentic-stlc.yml") -> str:
    """Trigger GHA workflow and return the run ID."""
    _run(["gh", "workflow", "run", workflow, "--repo", f"{owner}/{name}", "--ref", branch])
    time.sleep(5)
    result = subprocess.run(
        ["gh", "run", "list", "--repo", f"{owner}/{name}", "--branch", branch,
         "--workflow", workflow, "--limit", "1", "--json", "databaseId"],
        capture_output=True, text=True,
    )
    try:
        runs = json.loads(result.stdout)
        if runs:
            run_id = str(runs[0]["databaseId"])
            print(f"[orchestrator] triggered run #{run_id}")
            return run_id
    except Exception:
        pass
    return ""


def watch_run(owner: str, name: str, run_id: str) -> int:
    """Watch a GHA run until completion. Returns exit code."""
    monitor_url = f"https://github.com/{owner}/{name}/actions/runs/{run_id}"
    print(f"[orchestrator] monitoring: {monitor_url}")
    result = subprocess.run(
        ["gh", "run", "watch", run_id, "--repo", f"{owner}/{name}", "--exit-status"],
        check=False,
    )
    return result.returncode


def download_artifacts(owner: str, name: str, run_id: str, dest_dir: str) -> list[str]:
    """Download all artifacts from a run."""
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["gh", "run", "download", run_id, "--repo", f"{owner}/{name}", "--dir", dest_dir],
        check=False,
    )
    downloaded = [str(p) for p in Path(dest_dir).iterdir() if p.is_dir()]
    print(f"[orchestrator] downloaded {len(downloaded)} artifacts to {dest_dir}")
    return downloaded


# ---------------------------------------------------------------------------
# Stage C: Bootstrap .agentic/ in the target repo
# ---------------------------------------------------------------------------

def bootstrap_agentic_dir(
    workspace_path: str,
    profile: repo_analyzer.RepoProfile,
    requirement: str,
    config: dict,
) -> Path:
    """
    Create .agentic/ in the target repo with the full Agentic STLC pipeline.
    Returns the path to the .agentic/ directory.

    The target repo's own source tree is never touched. All pipeline files,
    Python deps, config, and the requirement input live under .agentic/.
    GitHub Actions runs everything from that subdirectory.
    """
    dest_agentic = Path(workspace_path) / ".agentic"
    dest_agentic.mkdir(parents=True, exist_ok=True)
    print(f"[Stage C] Bootstrapping {dest_agentic} ...")

    # 1. Copy core CI pipeline scripts
    _copy_ci_scripts(dest_agentic)

    # 2. Copy support files (requirements.txt, pytest.ini, conftest.py)
    _copy_support_files(dest_agentic)

    # 3. Write adapted hyperexecute.yaml
    _write_hyperexecute_yaml(dest_agentic, profile)

    # 4. Write adapted agentic-stlc.config.yaml
    _write_agentic_config(dest_agentic, profile, config)

    # 5. Write requirement into .agentic/requirements/search.txt
    _write_requirements_file(dest_agentic, requirement, profile)

    # 6. Initialize empty state files (pipeline populates them in CI)
    _init_state_files(dest_agentic)

    total = len(_PIPELINE_CI_FILES) + len(_PIPELINE_ROOT_FILES) + 5
    print(f"[Stage C] done -{total} files written to .agentic/")
    return dest_agentic


def _copy_ci_scripts(dest_agentic: Path) -> None:
    ci_dest = dest_agentic / "ci"
    ci_dest.mkdir(parents=True, exist_ok=True)
    for rel_path in _PIPELINE_CI_FILES:
        src = _REPO_ROOT / rel_path
        if not src.exists():
            print(f"  [warn] source not found: {rel_path}")
            continue
        # rel_path is "ci/foo.py" -dest_name is "foo.py"
        dest = ci_dest / src.name
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  copied {len(_PIPELINE_CI_FILES)} CI scripts -> .agentic/ci/")


def _copy_support_files(dest_agentic: Path) -> None:
    for src_rel, dest_rel in _PIPELINE_ROOT_FILES:
        src = _REPO_ROOT / src_rel
        if not src.exists():
            print(f"  [warn] source not found: {src_rel}")
            continue
        dest = dest_agentic / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  copied {len(_PIPELINE_ROOT_FILES)} support files")


def _write_hyperexecute_yaml(dest_agentic: Path, profile: repo_analyzer.RepoProfile) -> None:
    """
    Write .agentic/hyperexecute.yaml adapted for the target repo.

    Local mode (kane_mode == 'local' or TARGET_URL contains localhost):
      - tunnel: true  so LambdaTest cloud browsers reach the HE VM via LT tunnel
      - pre: starts the React app on the HE VM before tests run
      - playwright install uses --with-deps for system dependency coverage

    Cloud mode (deployed TARGET_URL):
      - No tunnel, no app startup — cloud browser navigates directly to the URL.
    """
    src = _REPO_ROOT / "hyperexecute.yaml"
    if not src.exists():
        print("  [warn] hyperexecute.yaml not found in orchestration repo")
        return

    content = src.read_text(encoding="utf-8")

    target_url = profile.target_url or profile.app_url_local or ""
    local_mode = (
        profile.kane_mode == "local"
        or "localhost" in target_url
        or not profile.target_url
    )

    # Point TARGET_URL at the target app
    if target_url:
        content = re.sub(r'TARGET_URL:.*', f'TARGET_URL: "{target_url}"', content)

    # Upgrade playwright install to include system deps
    content = content.replace(
        "playwright install chromium firefox webkit",
        "playwright install chromium --with-deps",
    ).replace(
        "playwright install chromium firefox",
        "playwright install chromium --with-deps",
    )

    if local_mode:
        # Add tunnel: true after runson line (if not already present)
        if "tunnel: true" not in content:
            content = re.sub(
                r"(runson:.*\n)",
                r"\1tunnel: true\n",
                content,
                count=1,
            )

        # Inject app startup commands into pre block
        wd = profile.app_working_dir or "."
        install_cmd = profile.install_cmd or "npm ci"
        start_cmd = profile.start_cmd or "npm start"
        port = profile.app_port or 3000

        app_pre = (
            f"  - cd ../{wd} && {install_cmd}\n"
            f"  - (cd ../{wd} && {start_cmd} &)\n"
            f"  - npx wait-on http://localhost:{port} --timeout 60000\n"
        )
        # Insert app startup right before playwright install (after pip install)
        if "playwright install" in content:
            content = re.sub(
                r"(  - playwright install)",
                app_pre + r"\1",
                content,
                count=1,
            )
        else:
            # Append to pre block
            content = re.sub(
                r"(pre:\n)((?:  -.*\n)*)",
                r"\1\2" + app_pre,
                content,
            )

    # Update job labels for the target repo
    repo_label = profile.name.lower()
    content = re.sub(
        r'jobLabel:.*?(?=\n\S|\Z)',
        f'jobLabel:\n  - {repo_label}\n  - agentic-stlc\n  - playwright',
        content,
        flags=re.DOTALL,
    )

    (dest_agentic / "hyperexecute.yaml").write_text(content, encoding="utf-8")
    print(f"  wrote .agentic/hyperexecute.yaml  (local_mode={local_mode}, tunnel={local_mode})")


def _write_agentic_config(
    dest_agentic: Path,
    profile: repo_analyzer.RepoProfile,
    config: dict,
) -> None:
    target_url = profile.target_url or profile.app_url_local or ""
    kane_cfg = config.get("kaneai", {})
    project_id = kane_cfg.get("project_id", "")
    folder_id = kane_cfg.get("folder_id", "")

    content = f"""\
# Agentic STLC Platform -Project Configuration
# Auto-generated by Agentic STLC repo-upgrade skill
# Repository: {profile.repo_url}

version: "1.0"

project:
  name: "{profile.name.lower()}-stlc"
  description: "Agentic STLC pipeline for {profile.name}"
  repository: "{profile.repo_url}"
  branch: "{profile.default_branch}"

requirements:
  format: "acceptance_criteria"
  paths:
    - "requirements/search.txt"
  output_path: "requirements/analyzed_requirements.json"
  encoding: "utf-8"

scenarios:
  path: "scenarios/scenarios.json"
  id_prefix: "SC"
  id_start: 1

framework:
  type: "playwright"
  language: "python"
  test_dir: "tests/playwright"
  test_file: "tests/playwright/test_powerapps.py"

target:
  url: "{target_url}"
  environment: "staging"

execution:
  provider: "hyperexecute"
  mode: "incremental"
  concurrency: 5
  timeout_seconds: 90
  retries: 1
  browsers:
    - chrome
  platforms:
    - windows10

hyperexecute:
  config_file: "hyperexecute.yaml"
  cli_path: "./hyperexecute"
  project: "{profile.name.lower()}-stlc"
  region: "us"

kaneai:
  enabled: true
  parallel_workers: 10
  timeout_seconds: 120
  project_id: "{project_id}"
  folder_id: "{folder_id}"
  use_testmd: true
  testmd_output_dir: "kane/testmd"

reporting:
  output_dir: "reports"
  formats:
    - json
    - markdown
  github_summary: true
  artifacts:
    - "reports/"
    - "scenarios/scenarios.json"
    - "tests/playwright/test_powerapps.py"

quality_gates:
  min_coverage_pct: 50
  min_pass_rate: 75
  max_flaky: 5
  require_critical_coverage: true
  max_high_risk_uncovered: 999
  min_he_pct: 0
  confidence:
    enabled: true
    gate_severity: "WARNING"
"""
    (dest_agentic / "agentic-stlc.config.yaml").write_text(content, encoding="utf-8")
    print("  wrote .agentic/agentic-stlc.config.yaml")


def _write_requirements_file(dest_agentic: Path, requirement: str, profile: repo_analyzer.RepoProfile) -> None:
    req_dir = dest_agentic / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    content = f"""\
Title: {profile.name} -Agentic STLC Requirements
# Auto-generated by Agentic STLC repo-upgrade skill

As a user
I want to use {profile.name}
So that I can complete my goals

Acceptance Criteria:
{requirement}
"""
    (req_dir / "search.txt").write_text(content, encoding="utf-8")
    print("  wrote .agentic/requirements/search.txt")


def _init_state_files(dest_agentic: Path) -> None:
    """Initialize empty state files -pipeline populates them during CI runs."""
    init = {
        "scenarios/scenarios.json": "[]",
        "requirements/analyzed_requirements.json": "[]",
        "kane/objectives.json": "{}",
    }
    for rel, content in init.items():
        p = dest_agentic / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(content, encoding="utf-8")
    (dest_agentic / "reports").mkdir(parents=True, exist_ok=True)
    print("  initialized state files (scenarios.json, analyzed_requirements.json, reports/)")


# ---------------------------------------------------------------------------
# Mode: analyze
# ---------------------------------------------------------------------------

def cmd_analyze(args: argparse.Namespace) -> None:
    profile = repo_analyzer.analyze(args.repo_url, getattr(args, "target_url", ""))
    print("\n=== RepoProfile ===")
    for k, v in profile.__dict__.items():
        print(f"  {k}: {v!r}")

    config = _load_config()
    workspace_dir = config.get("repo_upgrade", {}).get("workspace_dir", "agentic-stlc")

    if args.requirement:
        req_slug = _slug(args.requirement)
        branch = args.branch if args.branch else f"feature/agentic-{req_slug}"
        print(f"\n=== Workspace to be created: {workspace_dir}/ ===")
        print(f"  {workspace_dir}/")
        print(f"  +-- {profile.name}/                  <- target repo (cloned)")
        print(f"  |   +-- .agentic/                    <- complete Agentic STLC pipeline")
        print(f"  |   |   +-- ci/                      <- {len(_PIPELINE_CI_FILES)} pipeline scripts")
        print(f"  |   |   +-- requirements/search.txt  <- requirement input")
        print(f"  |   |   +-- hyperexecute.yaml         <- adapted HE config")
        print(f"  |   |   +-- agentic-stlc.config.yaml")
        print(f"  |   +-- .github/workflows/agentic-stlc.yml  <- 3-job GHA workflow")
        print(f"  +-- ci/                              <- {len(_ALL_WORKSPACE_CI_FILES)} scripts (local copy)")
        print(f"  +-- templates/kane/testmd/           <- base TestMD template")
        print(f"  +-- templates/hyperexecute/          <- base HE config template")
        print(f"  +-- requirements.txt + pytest.ini")
        print(f"  +-- agentic-stlc.config.yaml        <- adapted for {profile.name}")
        print(f"\n=== GHA Pipeline Flow (mirrors lambdapro/agentic-stlc-kane-hyperexecute) ===")
        print(f"  Branch:  {branch}")
        target = profile.target_url or profile.app_url_local or "http://localhost:3000"
        print(f"  Target:  {target}")
        print(f"  Job 1 (analyze)     <- KaneAI verifies requirement against {target}")
        print(f"  Job 2 (orchestrate) <- Scenario sync, test gen, HyperExecute, traceability")
        print(f"  Job 3 (summary)     <- Verdict: GREEN / YELLOW / RED")


# ---------------------------------------------------------------------------
# Mode: inject
# ---------------------------------------------------------------------------

def cmd_inject(args: argparse.Namespace, config: dict) -> str:
    """
    Bootstrap workspace, clone repo, inject Agentic STLC pipeline, push.
    Returns the path to the cloned target repo inside the workspace.

    Stages:
      A - Bootstrap local orchestration workspace (agentic-stlc/)
      B - Clone target repo into workspace
      C - Bootstrap .agentic/ inside target repo with complete pipeline
      D - Inject GHA workflow (.github/workflows/agentic-stlc.yml)
      E - Commit and push feature branch
    """
    workspace_dir = config.get("repo_upgrade", {}).get("workspace_dir", "agentic-stlc")
    target_url = getattr(args, "target_url", "")

    # Analyze repo first (read-only, no clone) so profile informs the workspace
    print("\n[Stage A-pre] Analyzing target repo (read-only)...")
    profile = repo_analyzer.analyze(args.repo_url, target_url)
    print(f"  {profile.owner}/{profile.name}  framework={profile.framework}  "
          f"pkg={profile.package_manager}  port={profile.app_port}  "
          f"kane_mode={profile.kane_mode}")

    # Stage A: Bootstrap local workspace
    workspace_root = Path(workspace_dir)
    bootstrap_local_workspace(workspace_root, profile, config)

    # Stage B: Clone target repo into workspace
    print(f"\n[Stage B] Cloning {profile.owner}/{profile.name} -> {workspace_dir}/{profile.name}/")
    workspace_path = clone_repo(args.repo_url, workspace_dir)

    branch = args.branch or f"feature/agentic-{_slug(args.requirement)}"
    create_branch(workspace_path, branch)
    print(f"  Branch: {branch}")

    # Stage C: Bootstrap .agentic/ with complete pipeline
    bootstrap_agentic_dir(workspace_path, profile, args.requirement, config)

    # Stage D: Inject GHA workflow
    print("\n[Stage D] Injecting GitHub Actions workflow...")
    kane_cfg = config.get("kaneai", {})
    project_id = os.getenv("KANE_PROJECT_ID", "") or kane_cfg.get("project_id", "")
    folder_id = os.getenv("KANE_FOLDER_ID", "") or kane_cfg.get("folder_id", "")
    workflow_rel = gha_injector.inject(
        repo_profile=profile,
        workspace_dir=workspace_path,
        kane_project_id=project_id,
        kane_folder_id=folder_id,
    )
    print(f"  {workflow_rel}")

    # Copy the generated workflow into workspace templates for reference
    wf_src = Path(workspace_path) / ".github" / "workflows" / "agentic-stlc.yml"
    wf_tmpl = workspace_root / "templates" / "workflows" / "agentic-stlc.yml"
    if wf_src.exists():
        wf_tmpl.write_text(wf_src.read_text(encoding="utf-8"), encoding="utf-8")

    # Stage E: Commit and push
    print("\n[Stage E] Committing and pushing...")
    req_slug = _slug(args.requirement)
    sha = commit_and_push(
        workspace_path, branch,
        f"feat: inject agentic STLC pipeline for [{req_slug}]\n\n"
        f"Requirement: {args.requirement}\n\n"
        f"Bootstraps the complete Agentic STLC pipeline into .agentic/ inside\n"
        f"the target repo. The project's own source tree is untouched. GitHub\n"
        f"Actions runs all stages (KaneAI verification, HyperExecute regression,\n"
        f"traceability, verdict) from .agentic/ci/ autonomously.\n\n"
        f"Pipeline mirrors: lambdapro/agentic-stlc-kane-hyperexecute (3-job)\n\n"
        f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>",
    )

    print(f"\n[inject complete]")
    print(f"  Workspace:  {workspace_root.resolve()}/")
    print(f"  Target repo: {workspace_path}")
    print(f"  Branch:     {branch} -> {sha[:8] if sha else 'n/a'}")
    print(f"  Pipeline:   .agentic/ ({len(_PIPELINE_CI_FILES)} CI scripts + config)")
    print(f"  Workflow:   .github/workflows/agentic-stlc.yml")
    print(f"  Auto-trigger: push to {branch} starts agentic-stlc.yml")
    return workspace_path


# ---------------------------------------------------------------------------
# Mode: run (full pipeline)
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace, config: dict) -> None:
    workspace_path = cmd_inject(args, config)

    profile = repo_analyzer.analyze(args.repo_url, getattr(args, "target_url", ""))
    branch = args.branch or f"feature/agentic-{_slug(args.requirement)}"
    workflow = "agentic-stlc.yml"

    # Stage F: Trigger GHA
    print(f"\n[Stage F] Triggering {workflow} on {profile.owner}/{profile.name}@{branch}...")
    run_id = trigger_workflow(profile.owner, profile.name, branch, workflow)
    if not run_id:
        print("[orchestrator] warning: could not retrieve run ID -check GHA manually")
        print(f"  https://github.com/{profile.owner}/{profile.name}/actions")
        return

    monitor_url = f"https://github.com/{profile.owner}/{profile.name}/actions/runs/{run_id}"
    print(f"\n  Monitor: {monitor_url}")
    print(f"\n  Pipeline stages:")
    print(f"    Job 1 (analyze)     -> Kane AI verifies acceptance criteria")
    print(f"    Job 2 (orchestrate) -> Scenario sync, test gen, HyperExecute, traceability")
    print(f"    Job 3 (summary)     -> GREEN / YELLOW / RED verdict")

    # Stage G: Watch until completion
    print("\n[Stage G] Watching pipeline...")
    exit_code = watch_run(profile.owner, profile.name, run_id)

    # Stage H: Download artifacts into workspace reports dir
    workspace_dir = config.get("repo_upgrade", {}).get("workspace_dir", "agentic-stlc")
    artifacts_dir = str(Path(workspace_dir) / "reports" / "gha_artifacts")
    print(f"\n[Stage H] Downloading artifacts -> {artifacts_dir}/")
    download_artifacts(profile.owner, profile.name, run_id, artifacts_dir)

    # Display verdict from pipeline-reports artifact
    rec_json = Path(artifacts_dir) / "pipeline-reports" / "reports" / "release_recommendation.json"
    if not rec_json.exists():
        rec_json = Path(artifacts_dir) / "pipeline-reports" / "release_recommendation.json"
    if rec_json.exists():
        result = json.loads(rec_json.read_text())
        print("\n=== Release Verdict ===")
        print(f"  Verdict:   {result.get('verdict', 'UNKNOWN')}")
        print(f"  Pass rate: {result.get('pass_rate', 0)}%")
        if result.get("failing_requirements"):
            print("\n  Failing requirements:")
            for req in result["failing_requirements"][:5]:
                print(f"    - {req}")

    matrix_md = Path(artifacts_dir) / "pipeline-reports" / "reports" / "traceability_matrix.md"
    if not matrix_md.exists():
        matrix_md = Path(artifacts_dir) / "pipeline-reports" / "traceability_matrix.md"
    if matrix_md.exists():
        lines = matrix_md.read_text().splitlines()[:15]
        print("\n=== Traceability Matrix (excerpt) ===")
        for line in lines:
            print(f"  {line}")

    print(f"\n[orchestrator] pipeline complete -exit_code={exit_code}")
    print(f"  GHA run: {monitor_url}")
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Repo Upgrade + Autonomous Validation Orchestrator")
    parser.add_argument("--repo-url", required=True, help="Target GitHub repo URL")
    parser.add_argument("--requirement", default="", help="Business requirement to inject and validate")
    parser.add_argument("--branch", default="", help="Feature branch name (auto-generated if omitted)")
    parser.add_argument("--target-url", default="", help="Deployed app URL for Kane to test against")
    parser.add_argument("--mode", choices=["analyze", "inject", "run"], default="run",
                        help="Pipeline mode: analyze (read-only), inject (no trigger), run (full)")
    args = parser.parse_args()

    config = _load_config()

    if args.mode == "analyze":
        cmd_analyze(args)
    elif args.mode == "inject":
        if not args.requirement:
            parser.error("--requirement is required for inject and run modes")
        cmd_inject(args, config)
    else:
        if not args.requirement:
            parser.error("--requirement is required for inject and run modes")
        cmd_run(args, config)


if __name__ == "__main__":
    main()
