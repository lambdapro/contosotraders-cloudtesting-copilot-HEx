"""
Analyzes any GitHub repository via gh api (no clone required).
Returns a RepoProfile describing the tech stack, test setup, and Kane mode.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field


@dataclass
class RepoProfile:
    repo_url: str
    owner: str
    name: str
    default_branch: str
    framework: str            # "react" | "next" | "vue" | "angular" | "unknown"
    package_manager: str      # "npm" | "pnpm" | "yarn" | "unknown"
    install_cmd: str          # e.g. "npm ci"
    start_cmd: str            # e.g. "npm start"
    build_cmd: str            # e.g. "npm run build"
    app_port: int             # e.g. 3000
    app_working_dir: str      # relative path inside repo, e.g. "src/ContosoTraders.Ui.Website"
    test_framework: str       # "playwright" | "cypress" | "unknown"
    test_dir: str             # relative to app_working_dir, e.g. "tests"
    playwright_config: str    # path relative to repo root, e.g. "src/.../playwright.config.ts"
    he_config: str            # path to existing HE yaml, or ""
    lt_integration: bool      # True if lambda.setup.ts/js detected
    existing_test_files: list[str] = field(default_factory=list)
    app_url_local: str = "http://localhost:3000"
    kane_mode: str = "local"  # "local" (no --ws-endpoint) | "cloud" (--ws-endpoint LT CDP)
    target_url: str = ""      # deployed URL if known; empty for local mode
    node_version: str = "18"
    he_runson: str = "win"    # inherited from existing HE config
    playwright_projects: list[str] = field(default_factory=list)  # LambdaTest project names


def _gh_api(path: str) -> dict | list | None:
    """Call gh api and return parsed JSON, or None on error."""
    import os
    env = {**os.environ}
    env.pop("GH_TOKEN", None)   # ignore stale/bad GH_TOKEN; use keyring auth
    try:
        result = subprocess.run(
            ["gh", "api", path],
            capture_output=True, text=True, timeout=30, env=env,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


def _gh_file(owner: str, name: str, path: str) -> str | None:
    """Fetch decoded file content from a GitHub repo."""
    data = _gh_api(f"repos/{owner}/{name}/contents/{path}")
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return None
    import base64
    try:
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except Exception:
        return None


def _gh_list(owner: str, name: str, path: str) -> list[dict]:
    """List directory contents from a GitHub repo."""
    data = _gh_api(f"repos/{owner}/{name}/contents/{path}")
    return data if isinstance(data, list) else []


def _find_files(owner: str, name: str, search_path: str, pattern: str) -> list[str]:
    """Recursively search for files matching a substring pattern under search_path."""
    results: list[str] = []
    items = _gh_list(owner, name, search_path)
    for item in items:
        if item.get("type") == "file" and pattern.lower() in item["name"].lower():
            results.append(item["path"])
        elif item.get("type") == "dir":
            # Only recurse one level for speed; deeper paths add latency
            sub = _gh_list(owner, name, item["path"])
            for sub_item in sub:
                if sub_item.get("type") == "file" and pattern.lower() in sub_item["name"].lower():
                    results.append(sub_item["path"])
    return results


def _p(work_dir: str, filename: str) -> str:
    """Build a repo-relative path, avoiding a leading slash when work_dir is empty."""
    return f"{work_dir}/{filename}" if work_dir else filename


def _detect_package_manager(owner: str, name: str, work_dir: str) -> str:
    items = _gh_list(owner, name, work_dir if work_dir else "")
    names = {i["name"] for i in items if i.get("type") == "file"}
    if "pnpm-lock.yaml" in names:
        return "pnpm"
    if "yarn.lock" in names:
        return "yarn"
    if "package-lock.json" in names:
        return "npm"
    return "npm"


def _detect_framework_and_start(owner: str, name: str, work_dir: str) -> tuple[str, str, str, int]:
    """Returns (framework, start_cmd, build_cmd, port)."""
    pkg_content = _gh_file(owner, name, _p(work_dir, "package.json"))
    if not pkg_content:
        return "unknown", "npm start", "npm run build", 3000
    try:
        pkg = json.loads(pkg_content)
    except json.JSONDecodeError:
        return "unknown", "npm start", "npm run build", 3000

    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    framework = "unknown"
    if "next" in deps:
        framework = "next"
    elif "react-scripts" in deps or "react" in deps:
        framework = "react"
    elif "@angular/core" in deps:
        framework = "angular"
    elif "vue" in deps:
        framework = "vue"

    scripts = pkg.get("scripts", {})
    start_cmd = scripts.get("start", scripts.get("dev", "npm start"))
    if not start_cmd.startswith("npm") and not start_cmd.startswith("npx"):
        start_cmd = f"npm start"
    build_cmd = "npm run build" if "build" in scripts else "npm run build"

    port = 3000
    if "next" == framework:
        port = 3000
    # Try to extract port from start script
    m = re.search(r"PORT[=\s]+(\d{4,5})", start_cmd)
    if m:
        port = int(m.group(1))

    return framework, start_cmd, build_cmd, port


def _detect_playwright_config(owner: str, name: str, work_dir: str) -> tuple[str, int, list[str]]:
    """Returns (playwright_config_path, port, lt_project_names)."""
    for filename in ("playwright.config.ts", "playwright.config.js"):
        content = _gh_file(owner, name, _p(work_dir, filename))
        if content:
            config_path = _p(work_dir, filename)
            port = 3000
            m = re.search(r"localhost[:/](\d{4,5})", content)
            if m:
                port = int(m.group(1))
            lt_projects = re.findall(r"['\"]([^'\"]+@lambdatest)['\"]", content)
            return config_path, port, lt_projects
    return "", 3000, []


def _find_playwright_workspace(owner: str, name: str, default_branch: str) -> str:
    """
    Find the directory containing playwright.config.ts anywhere in the repo.
    Strategy 1: git tree API (fast, single call, may be truncated for large repos).
    Strategy 2: BFS directory walk up to 3 levels deep (used when tree is truncated).
    Returns the directory path (empty string = repo root).
    """
    # Strategy 1: full git tree
    data = _gh_api(f"repos/{owner}/{name}/git/trees/{default_branch}?recursive=1")
    if data and isinstance(data, dict):
        for item in data.get("tree", []):
            path = item.get("path", "")
            if path.endswith("playwright.config.ts") or path.endswith("playwright.config.js"):
                parts = path.rsplit("/", 1)
                return parts[0] if len(parts) > 1 else ""
        if not data.get("truncated"):
            return ""  # not truncated → playwright config genuinely absent
        # Tree was truncated — fall through to BFS

    # Strategy 2: BFS directory scan (handles truncated trees from large repos)
    pw_names = {"playwright.config.ts", "playwright.config.js"}
    root_items = _gh_list(owner, name, "")
    for item in root_items:
        if item.get("type") == "file" and item.get("name") in pw_names:
            return ""  # root
        if item.get("type") != "dir":
            continue
        sub_items = _gh_list(owner, name, item["path"])
        for sub in sub_items:
            if sub.get("type") == "file" and sub.get("name") in pw_names:
                return item["path"]
            if sub.get("type") == "dir":
                deep = _gh_list(owner, name, sub["path"])
                for d in deep:
                    if d.get("type") == "file" and d.get("name") in pw_names:
                        return sub["path"]
    return ""


def _find_app_working_dir(owner: str, name: str) -> str:
    """Find the subdirectory that contains the frontend app (has package.json with react/next)."""
    # Check root first
    root_pkg = _gh_file(owner, name, "package.json")
    if root_pkg:
        try:
            pkg = json.loads(root_pkg)
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if any(k in deps for k in ("react", "react-scripts", "next", "vue", "@angular/core")):
                return ""
        except Exception:
            pass

    # Search one level deep for package.json in subdirs
    root_items = _gh_list(owner, name, "")
    for item in root_items:
        if item.get("type") != "dir":
            continue
        sub_items = _gh_list(owner, name, item["path"])
        sub_names = {i["name"] for i in sub_items}
        if "package.json" in sub_names:
            # Check if it's a frontend package
            pkg_content = _gh_file(owner, name, f"{item['path']}/package.json")
            if pkg_content:
                try:
                    pkg = json.loads(pkg_content)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if any(k in deps for k in ("react", "react-scripts", "next", "vue", "@angular/core", "playwright")):
                        return item["path"]
                except Exception:
                    pass
            # Recurse one more level
            for sub_item in sub_items:
                if sub_item.get("type") != "dir":
                    continue
                deep_items = _gh_list(owner, name, sub_item["path"])
                deep_names = {i["name"] for i in deep_items}
                if "package.json" in deep_names:
                    pkg_content = _gh_file(owner, name, f"{sub_item['path']}/package.json")
                    if pkg_content:
                        try:
                            pkg = json.loads(pkg_content)
                            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                            if any(k in deps for k in ("react", "react-scripts", "next", "vue", "@angular/core", "playwright")):
                                return sub_item["path"]
                        except Exception:
                            pass
    return ""


def _find_test_dir(owner: str, name: str, work_dir: str) -> tuple[str, list[str]]:
    """Returns (test_dir_relative_to_work_dir, list_of_spec_file_paths)."""
    for candidate in ("tests", "test", "e2e", "src/tests", "__tests__"):
        full_path = f"{work_dir}/{candidate}" if work_dir else candidate
        items = _gh_list(owner, name, full_path)
        specs = [i["path"] for i in items if i.get("type") == "file"
                 and (i["name"].endswith(".spec.ts") or i["name"].endswith(".spec.js")
                      or i["name"].endswith(".test.ts") or i["name"].endswith(".test.js"))]
        if specs:
            return candidate, specs
    return "tests", []


def _find_he_config(owner: str, name: str, work_dir: str) -> tuple[str, str]:
    """Returns (he_config_path, runson). Looks in work_dir and repo root."""
    for search_dir in ([work_dir] if work_dir else []) + [""]:
        items = _gh_list(owner, name, search_dir) if search_dir else _gh_list(owner, name, "")
        for item in items:
            fname = item.get("name", "")
            if item.get("type") == "file" and "hyperexecute" in fname.lower() and fname.endswith(".yaml"):
                path = item["path"]
                content = _gh_file(owner, name, path) or ""
                runson = "win"
                m = re.search(r"runson\s*:\s*(\S+)", content)
                if m:
                    runson = m.group(1).strip().strip('"\'')
                return path, runson
    return "", "win"


def _detect_node_version(owner: str, name: str, work_dir: str) -> str:
    for f in (".nvmrc", ".node-version"):
        content = _gh_file(owner, name, _p(work_dir, f))
        if content:
            v = content.strip().lstrip("v").split(".")[0]
            if v.isdigit():
                return v
    pkg_content = _gh_file(owner, name, _p(work_dir, "package.json"))
    if pkg_content:
        try:
            pkg = json.loads(pkg_content)
            engines = pkg.get("engines", {}).get("node", "")
            m = re.search(r"(\d+)", engines)
            if m:
                return m.group(1)
        except Exception:
            pass
    return "18"


def analyze(repo_url: str, target_url: str = "") -> RepoProfile:
    """
    Analyze a GitHub repository and return a RepoProfile.

    Args:
        repo_url: Full GitHub URL, e.g. https://github.com/owner/repo
        target_url: Optional deployed app URL; if empty, kane_mode is set to "local"
    """
    # Parse owner/name from URL
    m = re.search(r"github\.com/([^/]+)/([^/]+?)(?:\.git)?$", repo_url.rstrip("/"))
    if not m:
        raise ValueError(f"Cannot parse GitHub owner/name from: {repo_url}")
    owner, name = m.group(1), m.group(2)

    # Fetch repo metadata
    repo_data = _gh_api(f"repos/{owner}/{name}") or {}
    default_branch = repo_data.get("default_branch", "main")

    print(f"[repo_analyzer] Analyzing {owner}/{name} (branch: {default_branch})")

    # Detect app working directory:
    # Prefer the directory that contains playwright.config.ts (the actual test workspace).
    # Fall back to the directory that has the frontend package.json.
    pw_workspace = _find_playwright_workspace(owner, name, default_branch)
    work_dir = pw_workspace if pw_workspace else _find_app_working_dir(owner, name)
    print(f"[repo_analyzer] app_working_dir={work_dir!r} (playwright_workspace={pw_workspace!r})")

    # Package manager
    pkg_mgr = _detect_package_manager(owner, name, work_dir)
    install_cmd = {"npm": "npm ci", "pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install --frozen-lockfile"}.get(pkg_mgr, "npm ci")

    # Framework, start/build commands, port
    framework, start_cmd, build_cmd, port = _detect_framework_and_start(owner, name, work_dir)
    print(f"[repo_analyzer] framework={framework} start_cmd={start_cmd!r} port={port}")

    # Playwright config, port override, LT projects
    pw_config, pw_port, lt_projects = _detect_playwright_config(owner, name, work_dir)
    if pw_port:
        port = pw_port

    # Test framework detection
    test_framework = "playwright" if pw_config else "unknown"
    if not pw_config:
        # Check for cypress
        items = _gh_list(owner, name, work_dir)
        if any("cypress" in i.get("name", "").lower() for i in items):
            test_framework = "cypress"

    # LambdaTest integration
    lt_integration = False
    for fname in ("lambda.setup.ts", "lambda.setup.js", "lambdatest.setup.ts"):
        path = f"{work_dir}/{fname}" if work_dir else fname
        if _gh_file(owner, name, path):
            lt_integration = True
            break

    # Test directory + spec files
    test_dir, spec_files = _find_test_dir(owner, name, work_dir)

    # HyperExecute config
    he_config, he_runson = _find_he_config(owner, name, work_dir)

    # Node version
    node_version = _detect_node_version(owner, name, work_dir)

    # Kane mode
    kane_mode = "cloud" if target_url else "local"
    app_url_local = f"http://localhost:{port}"

    print(f"[repo_analyzer] pkg_manager={pkg_mgr} test_framework={test_framework} lt_integration={lt_integration}")
    print(f"[repo_analyzer] test_dir={test_dir} spec_files={len(spec_files)} he_config={he_config!r}")
    print(f"[repo_analyzer] kane_mode={kane_mode} node_version={node_version}")

    return RepoProfile(
        repo_url=repo_url,
        owner=owner,
        name=name,
        default_branch=default_branch,
        framework=framework,
        package_manager=pkg_mgr,
        install_cmd=install_cmd,
        start_cmd="npm start",  # normalize: always invoke via npm
        build_cmd=build_cmd,
        app_port=port,
        app_working_dir=work_dir,
        test_framework=test_framework,
        test_dir=test_dir,
        playwright_config=pw_config,
        he_config=he_config,
        lt_integration=lt_integration,
        existing_test_files=spec_files,
        app_url_local=app_url_local,
        kane_mode=kane_mode,
        target_url=target_url,
        node_version=node_version,
        he_runson=he_runson,
        playwright_projects=lt_projects,
    )


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/lambdapro/contosotraders-cloudtesting-copilot-HEx"
    profile = analyze(url)
    print("\n=== RepoProfile ===")
    for k, v in profile.__dict__.items():
        print(f"  {k}: {v!r}")
