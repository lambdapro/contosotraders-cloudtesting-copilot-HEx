"""
Fetch enriched job data from HyperExecute and Kane AI APIs.
Writes reports/api_details.json for use by write_github_summary.py.
Also writes reports/kane_result_SC-XXX.json per session so build_traceability.py
picks up real Selenium pass/fail when tests run on HyperExecute VMs.
"""
import base64
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path


def load_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    content = p.read_text(encoding="utf-8").strip()
    return json.loads(content) if content else default


def basic_auth_header():
    username = os.environ.get("LT_USERNAME", "")
    access_key = os.environ.get("LT_ACCESS_KEY", "")
    if not username or not access_key:
        return {}
    encoded = base64.b64encode(f"{username}:{access_key}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def get(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


_JOB_ID_RE = re.compile(r"hyperexecute\.lambdatest\.com/hyperexecute/task\?jobId=([\w-]+)")


def extract_he_job_id(failure_analysis_path="reports/hyperexecute_failure_analysis.md"):
    # Try failure analysis report first
    path = Path(failure_analysis_path)
    if path.exists():
        match = _JOB_ID_RE.search(path.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    # Fall back to CLI log (written by execute stage, downloaded as artifact)
    for cli_log in ("hyperexecute-cli.log", "reports/hyperexecute-cli.log"):
        log_path = Path(cli_log)
        if not log_path.exists():
            continue
        text = log_path.read_text(encoding="utf-8", errors="ignore")
        match = _JOB_ID_RE.search(text)
        if match:
            return match.group(1)
        match = re.search(r'"jobId"\s*:\s*"([\w-]+)"', text)
        if match:
            return match.group(1)
    return ""


def fetch_he_job(job_id, headers):
    """Fetch job summary from HyperExecute API."""
    for version in ("v2.0", "v1.0"):
        try:
            return get(f"https://api.hyperexecute.cloud/{version}/jobs/{job_id}", headers)
        except Exception:
            continue
    return {}


def fetch_he_sessions(job_id, headers):
    """
    Fetch all session-level details for a job using the v2.0 sessions endpoint.
    Endpoint: GET /v2.0/job/{jobID}/sessions
    Response: { data: [{sessionID, testID, taskID, name, scenario_name, status, duration}], metadata: {cursor, hasmore} }
    Uses cursor-based pagination to collect all sessions (max 20 per page).
    """
    sessions = []
    cursor = None
    page = 0
    while True:
        params = {"limit": 20}
        if cursor:
            params["cursor"] = cursor
        qs = urllib.parse.urlencode(params)
        url = f"https://api.hyperexecute.cloud/v2.0/job/{job_id}/sessions?{qs}"
        try:
            data = get(url, headers)
        except Exception as exc:
            print(f"  sessions API error (page {page}): {exc}")
            break
        page_sessions = data.get("data", [])
        sessions.extend(page_sessions)
        metadata = data.get("metadata", {})
        print(f"  sessions page {page}: {len(page_sessions)} sessions (total so far: {len(sessions)})")
        if not metadata.get("hasmore"):
            break
        cursor = metadata.get("cursor")
        if not cursor:
            break
        page += 1
    return sessions


def fetch_kane_session(session_id, headers):
    """Fetch Kane AI session details by session ID."""
    for url in (
        f"https://api.kaneai.lambdatest.com/api/v1/he-sessions/{session_id}",
        f"https://api.kaneai.lambdatest.com/api/v1/sessions/{session_id}",
    ):
        try:
            return get(url, headers)
        except Exception:
            continue
    return {}


def extract_session_id(kane_link):
    """Extract session/run ID from a Kane AI URL."""
    if not kane_link:
        return ""
    match = re.search(r"[?&/](?:sessionId|runId|id)=?([\w-]+)", kane_link)
    return match.group(1) if match else ""


def _sc_id_from_name(name):
    """Map a pytest function name like test_sc_001_* to SC-001."""
    m = re.search(r"test_sc_(\d+)", name or "", re.IGNORECASE)
    return f"SC-{int(m.group(1)):03d}" if m else None


def main():
    headers = basic_auth_header()
    requirements = load_json("requirements/analyzed_requirements.json", [])
    scenarios = load_json("scenarios/scenarios.json", [])

    job_id = extract_he_job_id()
    he_job = {}
    he_sessions = []

    if job_id and headers:
        print(f"Fetching HyperExecute job {job_id} via API...")
        he_job = fetch_he_job(job_id, headers)
        print(f"  job keys: {list(he_job.keys())}")
        he_sessions = fetch_he_sessions(job_id, headers)
        print(f"  total sessions fetched: {len(he_sessions)}")
    elif not job_id:
        print("No HyperExecute job ID found — skipping API fetch.")
    else:
        print("No LT credentials — skipping API fetch.")

    # ── Write per-scenario result files from session API data ─────────────────
    # build_traceability.py reads reports/kane_result_SC-*.json.
    # When tests run on HyperExecute VMs, conftest files never reach the Actions
    # runner, so we recreate them here from the sessions API.
    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    task_results = []
    for session in he_sessions:
        # scenario_name is the pytest function name; use it for SC-XXX mapping
        scenario_name = session.get("scenario_name") or session.get("name", "")
        status = session.get("status", "unknown")
        test_id = session.get("testID", "")
        session_id = session.get("sessionID", "")
        task_id = session.get("taskID", "")
        # Session link: LambdaTest Automate session URL
        session_link = (
            f"https://automation.lambdatest.com/test?testID={test_id}"
            if test_id else ""
        )

        task_results.append({
            "task_id": task_id,
            "name": scenario_name,
            "status": status,
            "session_link": session_link,
        })

        sc_id = _sc_id_from_name(scenario_name)
        if not sc_id:
            continue
        scenario = next((s for s in scenarios if s["id"] == sc_id), {})
        result_path = reports_dir / f"kane_result_{sc_id}.json"
        # Conftest-written files from local runs take priority
        if not result_path.exists():
            result_path.write_text(json.dumps({
                "requirement_id": scenario.get("requirement_id", sc_id),
                "scenario_id": sc_id,
                "test_case_id": scenario.get("test_case_id", ""),
                "status": status,
                "link": session_link,
            }, indent=2) + "\n", encoding="utf-8")
            print(f"  wrote {result_path}  status={status}  testID={test_id}")

    # ── Fetch Kane AI session details ─────────────────────────────────────────
    kane_sessions = []
    if headers:
        for req in requirements:
            for link in req.get("kane_links", []):
                if not link:
                    continue
                sid = extract_session_id(link)
                detail = fetch_kane_session(sid, headers) if sid else {}
                if sid:
                    print(f"Fetching Kane session {sid}...")
                kane_sessions.append({
                    "requirement_id": req["id"],
                    "link": link,
                    "session_id": sid,
                    "detail": detail,
                })

    # ── HyperExecute job summary ──────────────────────────────────────────────
    he_summary = {}
    if he_job or job_id:
        raw_status = (
            he_job.get("status")
            or (he_job.get("summary") or {}).get("status")
            or (he_job.get("data") or {}).get("status")
            or "unknown"
        )
        total = (
            he_job.get("totalTasks")
            or he_job.get("total_tasks")
            or len(he_sessions)
        )
        passed_count = sum(1 for s in he_sessions if s.get("status") == "passed")
        failed_count = total - passed_count if total else 0
        he_summary = {
            "job_id": job_id,
            "job_link": f"https://hyperexecute.lambdatest.com/hyperexecute/task?jobId={job_id}",
            "selenium_reports_link": (
                f"https://hyperexecute.lambdatest.com/artifact/view/{job_id}"
                "?artifactName=selenium-test-reports"
            ),
            "runtime_logs_link": (
                f"https://hyperexecute.lambdatest.com/artifact/view/{job_id}"
                "?artifactName=hyperexecute-runtime"
            ),
            "status": raw_status,
            "total_tasks": total,
            "passed_tasks": passed_count,
            "failed_tasks": failed_count,
            "raw": he_job,
        }

    out = {
        "he_summary": he_summary,
        "he_tasks": task_results,
        "kane_sessions": kane_sessions,
    }

    out_path = Path("reports/api_details.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {out_path}  "
        f"(job_id={job_id}, sessions={len(task_results)}, kane_sessions={len(kane_sessions)})"
    )


if __name__ == "__main__":
    main()
