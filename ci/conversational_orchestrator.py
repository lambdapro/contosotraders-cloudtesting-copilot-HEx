#!/usr/bin/env python3
"""
Agentic STLC Conversational Orchestrator
=========================================
Entry point for the Claude Code "agentic-stlc" skill.  Handles every
interaction mode the skill needs without continuous polling:

  --mode git-commit          Stage, commit, and push requirements to branch
  --mode trigger             Dispatch GitHub Actions workflow_dispatch
  --mode watch-events        Tail NDJSON event file and stream to stdout
  --mode collect-results     Download artifacts + format chat report
  --mode rca                 Run RCA skill and print actionable findings
  --mode stop-webhook        Send SIGTERM to the webhook server process
  --mode full                Run all modes sequentially (CI / local testing)

Event-driven design
-------------------
Instead of polling GitHub's REST API in a tight loop, the orchestrator:
  1. Starts a local webhook server (ci/webhook_server.py) that receives
     POST callbacks from the GitHub Actions workflow at each stage.
  2. Tails reports/.webhook_events.ndjson in watch-events mode and prints
     formatted progress lines as each event arrives.
  3. Uses `gh run watch` as a zero-polling fallback when webhooks are
     unavailable (e.g., no callback URL reachable from GitHub Actions).

GitHub Actions trigger uses workflow_dispatch + repository_dispatch so
no long-running process is needed on the client side.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

REPO          = "lambdapro/agentic-stlc-kane-hyperexecute"
WORKFLOW_FILE = "agentic-stlc-on-demand.yml"
PRODUCT_BRANCH = "product"
EVENT_FILE    = Path("reports/.webhook_events.ndjson")
REQ_FILE      = Path("requirements/search.txt")
REPORTS_DIR   = Path("reports")
ANALYZED_REQ  = Path("requirements/analyzed_requirements.json")

# ANSI colours (no-op on Windows if not in terminal)
_IS_TTY = sys.stdout.isatty()
GREEN  = "\033[32m" if _IS_TTY else ""
YELLOW = "\033[33m" if _IS_TTY else ""
RED    = "\033[31m" if _IS_TTY else ""
CYAN   = "\033[36m" if _IS_TTY else ""
BOLD   = "\033[1m"  if _IS_TTY else ""
RESET  = "\033[0m"  if _IS_TTY else ""

# Stage labels for progress display
_STAGE_LABELS: dict[str, str] = {
    "pipeline_started":            "[1/7] Pipeline started",
    "analyze":                     "[1/7] KaneAI analysis",
    "scenarios":                   "[2/7] Scenario sync",
    "playwright":                  "[3/7] Test generation",
    "selection":                   "[4/7] Test selection",
    "hyperexecute_submitted":      "[5/7] HyperExecute submitted",
    "hyperexecute_done":           "[6/7] HyperExecute complete",
    "traceability":                "[7/7] Traceability built",
    "pipeline_complete":           "Pipeline complete",
    "pipeline_failed":             "Pipeline failed",
}


# ── Requirement format helpers ─────────────────────────────────────────────────

def _format_requirements_file(criteria: list[str], source_label: str = "Chat") -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"Title: {source_label} — {today}",
        f"# submitted via Agentic STLC Skill on {today}",
        "",
        "As a user",
        "I want to use the application",
        "So that I can complete my goals",
        "",
        "Acceptance Criteria:",
    ]
    lines.extend(criteria)
    return "\n".join(lines) + "\n"


# ── Git operations ─────────────────────────────────────────────────────────────

def _run_git(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=60)


def git_commit_and_push(branch: str, message: str, files: list[str]) -> dict:
    """Checkout or create branch, stage files, commit, push. Returns result dict."""
    # Fetch latest
    _run_git(["git", "fetch", "--quiet"], check=False)

    # Checkout branch (create if needed)
    local = _run_git(["git", "branch", "--list", branch], check=False)
    if branch in local.stdout:
        _run_git(["git", "checkout", branch], check=False)
    else:
        remote = _run_git(["git", "branch", "-r", "--list", f"origin/{branch}"], check=False)
        if f"origin/{branch}" in remote.stdout:
            _run_git(["git", "checkout", "-b", branch, f"origin/{branch}"])
        else:
            _run_git(["git", "checkout", "-b", branch])

    # Stage
    existing = [f for f in files if Path(f).exists()]
    if not existing:
        return {"success": False, "error": "no files to commit"}
    _run_git(["git", "add"] + existing)

    # Commit
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "Agentic STLC Skill",
           "GIT_AUTHOR_EMAIL": "skill@lambdatest.com",
           "GIT_COMMITTER_NAME": "Agentic STLC Skill",
           "GIT_COMMITTER_EMAIL": "skill@lambdatest.com"}
    r = subprocess.run(["git", "commit", "-m", message],
                       capture_output=True, text=True, env=env, timeout=30)
    if r.returncode != 0 and "nothing to commit" not in r.stdout + r.stderr:
        return {"success": False, "error": r.stderr or r.stdout}

    sha_r = _run_git(["git", "rev-parse", "HEAD"], check=False)
    sha = sha_r.stdout.strip()

    # Push
    push_r = _run_git(["git", "push", "origin", branch, "--set-upstream"], check=False)
    pushed = push_r.returncode == 0
    return {"success": True, "sha": sha, "pushed": pushed,
            "push_error": push_r.stderr if not pushed else ""}


# ── GitHub Actions trigger ─────────────────────────────────────────────────────

def trigger_workflow(
    branch: str,
    workflow: str,
    callback_url: str,
    full_run: bool,
    token: str,
) -> dict:
    """Dispatch workflow via GitHub REST API. Returns {run_id, html_url}."""
    try:
        import httpx
    except ImportError:
        # Fall back to gh CLI
        return _trigger_via_gh_cli(branch, workflow, callback_url, full_run)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "ref": branch,
        "inputs": {
            "full_run": str(full_run).lower(),
            "callback_url": callback_url,
        },
    }
    resp = httpx.post(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/dispatches",
        headers=headers,
        json=payload,
        timeout=30,
    )
    if resp.status_code != 204:
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

    # Wait briefly then find the run that was just created
    time.sleep(4)
    runs_resp = httpx.get(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/runs",
        headers=headers,
        params={"per_page": 5, "branch": branch},
        timeout=30,
    )
    run_id = ""
    html_url = ""
    if runs_resp.status_code == 200:
        runs = runs_resp.json().get("workflow_runs", [])
        if runs:
            run_id = str(runs[0]["id"])
            html_url = runs[0]["html_url"]

    return {"success": True, "run_id": run_id, "html_url": html_url}


def _trigger_via_gh_cli(branch: str, workflow: str, callback_url: str, full_run: bool) -> dict:
    """Fallback: use gh CLI to dispatch."""
    cmd = [
        "gh", "workflow", "run", workflow,
        "--repo", REPO,
        "--ref", branch,
        "-f", f"full_run={str(full_run).lower()}",
        "-f", f"callback_url={callback_url}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"success": False, "error": r.stderr or r.stdout}

    # Get the run ID
    time.sleep(4)
    list_r = subprocess.run(
        ["gh", "run", "list", "--workflow", workflow, "--repo", REPO,
         "--branch", branch, "--limit", "1", "--json", "databaseId,url"],
        capture_output=True, text=True, timeout=30,
    )
    run_id = ""
    html_url = ""
    if list_r.returncode == 0 and list_r.stdout.strip():
        try:
            data = json.loads(list_r.stdout)
            if data:
                run_id = str(data[0].get("databaseId", ""))
                html_url = data[0].get("url", "")
        except json.JSONDecodeError:
            pass

    return {"success": True, "run_id": run_id, "html_url": html_url}


# ── Event watching ─────────────────────────────────────────────────────────────

def _format_event(event: dict) -> str:
    """Convert a pipeline event dict to a human-readable chat line."""
    ev   = event.get("event", "")
    data = event.get("data", {})
    ts   = event.get("timestamp", "")
    label = _STAGE_LABELS.get(ev) or _STAGE_LABELS.get(data.get("stage", "")) or ev

    if ev == "pipeline_started":
        run_id = data.get("run_id", "")
        return f"{CYAN}{BOLD}{label}{RESET} — run #{run_id}"

    if ev == "stage_complete":
        stage = data.get("stage", "")
        label = _STAGE_LABELS.get(stage, f"[?] {stage}")
        extra = ""
        if stage == "analyze":
            n, p = data.get("total", "?"), data.get("passed", "?")
            extra = f" — {p}/{n} Kane verifications passed"
        elif stage == "scenarios":
            extra = f" — {data.get('new', 0)} new, {data.get('updated', 0)} updated"
        elif stage == "playwright":
            extra = f" — {data.get('test_count', '?')} test functions"
        elif stage == "selection":
            extra = f" — {data.get('selected', '?')} tests queued"
        elif stage == "hyperexecute_submitted":
            he_url = data.get("he_url", "")
            extra  = f"\n  HyperExecute job: {he_url}" if he_url else ""
        elif stage == "hyperexecute_done":
            p, t = data.get("passed", "?"), data.get("total", "?")
            extra = f" — {p}/{t} tests passed"
        elif stage == "traceability":
            extra = f" — {data.get('coverage', '?')}% coverage"
        return f"{CYAN}{label}{RESET}{extra}"

    if ev == "pipeline_complete":
        verdict = data.get("verdict", "UNKNOWN")
        colour = GREEN if verdict == "GREEN" else (YELLOW if verdict == "YELLOW" else RED)
        rate   = data.get("pass_rate", "?")
        run_id = data.get("run_id", "")
        url    = data.get("html_url", "")
        lines  = [
            "",
            f"{BOLD}{'━'*44}{RESET}",
            f"  {colour}{BOLD}PIPELINE VERDICT: {verdict}{RESET}",
            f"  Pass rate: {rate}%",
            f"  Run: #{run_id}",
        ]
        if url:
            lines.append(f"  Details: {url}")
        lines.append(f"{BOLD}{'━'*44}{RESET}")
        return "\n".join(lines)

    if ev == "pipeline_failed":
        msg = data.get("message", "pipeline encountered an error")
        return f"{RED}{BOLD}[FAILED]{RESET} {msg}"

    return f"[event] {ev}: {json.dumps(data)[:120]}"


def watch_events(event_file: Path, timeout_s: int = 1800) -> None:
    """
    Tail event_file line by line and print formatted messages.
    Returns when pipeline_complete or pipeline_failed is received,
    or when timeout is reached.
    Uses os.stat() mtime change detection — no polling sleep loop.
    """
    event_file.parent.mkdir(parents=True, exist_ok=True)
    # Wait for the file to appear (pipeline may not have started yet)
    deadline = time.monotonic() + timeout_s
    while not event_file.exists() and time.monotonic() < deadline:
        time.sleep(1)

    if not event_file.exists():
        print(f"{YELLOW}[watch] event file not found after {timeout_s}s — pipeline may not have started{RESET}")
        return

    seen_lines = 0
    last_mtime = 0.0
    terminal_received = False

    while time.monotonic() < deadline and not terminal_received:
        try:
            mtime = event_file.stat().st_mtime
        except FileNotFoundError:
            time.sleep(1)
            continue

        if mtime > last_mtime:
            last_mtime = mtime
            lines = event_file.read_text(encoding="utf-8").splitlines()
            for line in lines[seen_lines:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                print(_format_event(ev), flush=True)
                seen_lines += 1
                if ev.get("event") in ("pipeline_complete", "pipeline_failed"):
                    terminal_received = True
                    break
        else:
            time.sleep(0.5)

    if not terminal_received:
        print(f"{YELLOW}[watch] timed out after {timeout_s}s waiting for pipeline completion{RESET}")


# ── Artifact collection + results formatting ───────────────────────────────────

def collect_results(run_id: str, token: str) -> dict:
    """Download pipeline-reports artifact and format results for chat."""
    if not run_id:
        return _load_local_results()

    try:
        import httpx
    except ImportError:
        return _load_local_results()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # List artifacts
    resp = httpx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/artifacts",
        headers=headers, timeout=30,
    )
    if resp.status_code != 200:
        print(f"[collect] artifact API returned {resp.status_code} — using local reports", file=sys.stderr)
        return _load_local_results()

    import io, zipfile
    artifacts = resp.json().get("artifacts", [])
    for art in artifacts:
        if "pipeline-reports" in art.get("name", "").lower() or "report" in art.get("name", "").lower():
            dl_resp = httpx.get(
                art["archive_download_url"],
                headers=headers, timeout=120, follow_redirects=True,
            )
            if dl_resp.status_code == 200:
                dest = REPORTS_DIR / f"ci-run-{run_id}" / art["name"]
                dest.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(io.BytesIO(dl_resp.content)) as zf:
                    zf.extractall(str(dest))
                print(f"[collect] artifacts downloaded to {dest}")
                break

    return _load_local_results()


def _load_local_results() -> dict:
    """Load results from local report files."""
    tm_path = REPORTS_DIR / "traceability_matrix.json"
    rr_path = REPORTS_DIR / "release_recommendation.md"
    he_path = REPORTS_DIR / "api_details.json"

    results: dict = {"verdict": "UNKNOWN", "pass_rate": 0, "requirements": []}

    if tm_path.exists():
        try:
            tm = json.loads(tm_path.read_text(encoding="utf-8"))
            results["requirements"] = tm if isinstance(tm, list) else tm.get("requirements", [])
        except Exception:
            pass

    if rr_path.exists():
        text = rr_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("**Verdict:**"):
                results["verdict"] = line.split(":")[-1].strip().strip("*")
            m = re.search(r"Pass rate:\s*([\d.]+)%", line)
            if m:
                results["pass_rate"] = float(m.group(1))

    if he_path.exists():
        try:
            he = json.loads(he_path.read_text(encoding="utf-8"))
            results["he_job_id"] = he.get("he_job_id", "")
            results["he_tasks"]  = he.get("he_tasks", [])
        except Exception:
            pass

    return results


def print_results(results: dict, run_id: str = "", html_url: str = "") -> None:
    """Render results to stdout in chat-friendly format."""
    verdict = results.get("verdict", "UNKNOWN")
    rate    = results.get("pass_rate", 0)
    reqs    = results.get("requirements", [])
    he_id   = results.get("he_job_id", "")

    colour = GREEN if verdict == "GREEN" else (YELLOW if verdict == "YELLOW" else RED)

    print(f"\n{BOLD}{'━'*48}{RESET}")
    print(f"  {colour}{BOLD}PIPELINE VERDICT: {verdict}{RESET}")
    print(f"  Pass rate: {rate}%  |  Requirements: {len(reqs)}")
    if run_id:
        print(f"  Run: #{run_id}")
    if html_url:
        print(f"  GitHub Actions: {html_url}")
    if he_id:
        print(f"  HyperExecute:   https://hyperexecute.lambdatest.com/hyperexecute/task?jobId={he_id}")
    print(f"{BOLD}{'━'*48}{RESET}\n")

    if reqs:
        print(f"{'Req ID':8}  {'Kane':8}  {'Playwright':11}  {'Overall':8}  Criterion")
        print("-" * 80)
        for r in reqs:
            rid      = r.get("requirement_id", r.get("id", "?"))
            kane     = r.get("kane_status", r.get("kane_verify", "?"))
            pw       = r.get("playwright_status", r.get("playwright", "?"))
            overall  = r.get("overall_status", r.get("overall", "?"))
            desc     = r.get("description", r.get("acceptance_criterion", ""))[:55]
            col      = GREEN if overall in ("passed", "GREEN") else (YELLOW if overall == "YELLOW" else RED)
            print(f"{rid:8}  {kane:8}  {pw:11}  {col}{overall:8}{RESET}  {desc}")
        print()

    # Session links for failures
    failed = [r for r in reqs if r.get("overall_status", r.get("overall", "")) not in ("passed", "GREEN")]
    if failed:
        print(f"{BOLD}Failed session links:{RESET}")
        for r in failed:
            rid  = r.get("requirement_id", r.get("id", "?"))
            url  = r.get("session_url", r.get("lt_session", ""))
            kane = r.get("kane_session_url", r.get("kane_session", ""))
            if kane:
                print(f"  {rid} Kane:       {kane}")
            if url:
                print(f"  {rid} Playwright: {url}")
        print()


# ── RCA ────────────────────────────────────────────────────────────────────────

def run_rca() -> None:
    """Invoke RCA skill and print actionable findings to stdout."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from skills.rca import RCASkill
        skill = RCASkill(config=None)
        result = skill.run()
    except Exception as exc:
        print(f"{YELLOW}[rca] skill error: {exc} — falling back to rca_summary.md{RESET}", file=sys.stderr)
        _print_rca_from_file()
        return

    total = result.get("total_failures", 0)
    if total == 0:
        print(f"{GREEN}All requirements passed — no RCA needed.{RESET}\n")
        return

    rca_path = REPORTS_DIR / "rca_report.json"
    if not rca_path.exists():
        _print_rca_from_file()
        return

    report = json.loads(rca_path.read_text(encoding="utf-8"))
    print(f"\n{BOLD}{'━'*48}{RESET}")
    print(f"{BOLD}Root Cause Analysis — {total} failure(s){RESET}")
    print(f"{'━'*48}\n")

    for f in report.get("failures", []):
        rid    = f.get("requirement_id", f.get("test", "?"))
        cat    = f.get("category", "UNKNOWN")
        advice = f.get("advice", "")
        msg    = f.get("message", "")[:200]
        url    = f.get("session_url", "")
        src    = f.get("source", "").upper()

        colour = RED if cat in ("SELECTOR", "TIMEOUT", "AUTH") else YELLOW
        print(f"{colour}{BOLD}[{src}] {rid} — {cat}{RESET}")
        if advice:
            print(f"  Remediation: {advice}")
        if msg:
            print(f"  Message:     {msg}")
        if url:
            print(f"  Session:     {url}")
        print()


def _print_rca_from_file() -> None:
    path = REPORTS_DIR / "rca_summary.md"
    if path.exists():
        print(path.read_text(encoding="utf-8"))
    else:
        print(f"{YELLOW}[rca] no RCA data available — check reports/ directory{RESET}")


# ── Webhook server management ─────────────────────────────────────────────────

def stop_webhook(port: int) -> None:
    """Send termination signal to the webhook server on the given port."""
    try:
        import httpx
        httpx.post(f"http://localhost:{port}/_shutdown", timeout=3)
        print(f"[webhook] shutdown signal sent to port {port}")
    except Exception:
        # Process may already be gone
        pass


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Agentic STLC Conversational Orchestrator")
    p.add_argument("--mode", required=True,
                   choices=["git-commit", "trigger", "watch-events",
                            "collect-results", "rca", "stop-webhook", "full"],
                   help="Execution mode")
    p.add_argument("--branch",        default=PRODUCT_BRANCH, help="Target git branch")
    p.add_argument("--message",       default="feat: skill-submitted requirements", help="Git commit message")
    p.add_argument("--workflow",      default=WORKFLOW_FILE,  help="GitHub Actions workflow file name")
    p.add_argument("--callback-url",  default="",             help="Webhook callback URL for pipeline events")
    p.add_argument("--full-run",      action="store_true",    help="Run all scenarios, not just incremental")
    p.add_argument("--run-id",        default="",             help="GitHub Actions run ID (for collect-results)")
    p.add_argument("--event-file",    default=str(EVENT_FILE),help="Path to NDJSON event file")
    p.add_argument("--timeout",       type=int, default=1800, help="Max seconds to wait for pipeline")
    p.add_argument("--port",          type=int, default=8765, help="Webhook server port (for stop-webhook)")
    p.add_argument("--files",         nargs="*", default=[str(REQ_FILE), "scenarios/scenarios.json"],
                   help="Files to commit in git-commit mode")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")

    # ── git-commit ─────────────────────────────────────────────────────────────
    if args.mode == "git-commit":
        if not args.files:
            print("[git] no files specified", file=sys.stderr)
            sys.exit(1)
        result = git_commit_and_push(args.branch, args.message, args.files)
        if not result["success"]:
            print(f"[git] error: {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        sha = result.get("sha", "")
        pushed = result.get("pushed", False)
        status = "pushed" if pushed else f"committed (push failed: {result.get('push_error', '')})"
        print(f"[git] {status} to origin/{args.branch} — SHA: {sha}")
        if not pushed:
            sys.exit(1)

    # ── trigger ────────────────────────────────────────────────────────────────
    elif args.mode == "trigger":
        if not token:
            print("[trigger] GITHUB_TOKEN not set — cannot dispatch workflow", file=sys.stderr)
            sys.exit(1)
        result = trigger_workflow(
            branch=args.branch,
            workflow=args.workflow,
            callback_url=args.callback_url,
            full_run=args.full_run,
            token=token,
        )
        if not result.get("success"):
            print(f"[trigger] failed: {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        run_id   = result.get("run_id", "")
        html_url = result.get("html_url", "")
        print(f"[pipeline] triggered run #{run_id} on branch {args.branch}")
        print(f"[pipeline] monitor: {html_url}")
        # Write a startup event so watch-events has something to tail immediately
        _emit_local_event("pipeline_started", {"run_id": run_id, "branch": args.branch, "html_url": html_url})

    # ── watch-events ───────────────────────────────────────────────────────────
    elif args.mode == "watch-events":
        watch_events(Path(args.event_file), timeout_s=args.timeout)

    # ── collect-results ────────────────────────────────────────────────────────
    elif args.mode == "collect-results":
        results  = collect_results(args.run_id, token)
        html_url = f"https://github.com/{REPO}/actions/runs/{args.run_id}" if args.run_id else ""
        print_results(results, run_id=args.run_id, html_url=html_url)

    # ── rca ────────────────────────────────────────────────────────────────────
    elif args.mode == "rca":
        run_rca()

    # ── stop-webhook ───────────────────────────────────────────────────────────
    elif args.mode == "stop-webhook":
        stop_webhook(args.port)

    # ── full (CI / testing) ────────────────────────────────────────────────────
    elif args.mode == "full":
        # 1. commit
        result = git_commit_and_push(args.branch, args.message, args.files)
        if not result.get("success"):
            print(f"[full] git failed: {result.get('error')}", file=sys.stderr)
            sys.exit(1)

        # 2. trigger
        if token:
            t_result = trigger_workflow(args.branch, args.workflow,
                                        args.callback_url, args.full_run, token)
            run_id  = t_result.get("run_id", "")
            html_url = t_result.get("html_url", "")
            print(f"[full] triggered run #{run_id}")
        else:
            run_id = ""
            html_url = ""
            print("[full] GITHUB_TOKEN not set — skipping trigger")

        # 3. watch
        watch_events(Path(args.event_file), timeout_s=args.timeout)

        # 4. collect
        results = collect_results(run_id, token)
        print_results(results, run_id=run_id, html_url=html_url)

        # 5. rca
        run_rca()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _emit_local_event(event_type: str, data: dict) -> None:
    """Write an event to the local event file (for startup events etc.)."""
    EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = json.dumps({
        "event":     event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data":      data,
    })
    with EVENT_FILE.open("a", encoding="utf-8") as f:
        f.write(record + "\n")


if __name__ == "__main__":
    main()
