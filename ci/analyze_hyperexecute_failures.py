import argparse
import base64
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-json", default="reports/hyperexecute-result.json")
    parser.add_argument("--junit-dir", default="reports")
    parser.add_argument("--cli-log", default="hyperexecute-cli.log")
    parser.add_argument("--out", default="reports/hyperexecute_failure_analysis.md")
    return parser.parse_args()


def load_result(path):
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def junit_failures(root_dir):
    root = Path(root_dir)
    failures = []
    for xml_file in root.rglob("*.xml"):
        try:
            tree = ET.fromstring(xml_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for testcase in tree.iter("testcase"):
            failure = testcase.find("failure") or testcase.find("error")
            if failure is None:
                continue
            message = failure.attrib.get("message", "") or failure.text or ""
            lowered = message.lower()
            category = "assertion"
            reason = "Assertion or validation failure"
            if "elementclickinterceptedexception" in lowered:
                category = "ui-intercept"
                reason = "Clickable element was covered or not interactable in the current layout."
            elif "timeout" in lowered:
                category = "timeout"
                reason = "The page did not reach the expected state before the wait timed out."
            elif "auth gate" in lowered or "log in" in lowered:
                category = "auth-or-layout"
                reason = "Guest-visible content assumptions did not match the live page state."
            failures.append(
                {
                    "test": testcase.attrib.get("name", "unknown"),
                    "category": category,
                    "reason": reason,
                    "message": message.strip(),
                    "source": str(xml_file),
                }
            )
    return failures


def cli_highlights(path):
    file_path = Path(path)
    if not file_path.exists():
        return []
    raw_lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    highlights = []
    patterns = [r"(https://\S+)", r"Job Link:\s+(.+)$", r"remark:\s*(.+)$", r"Exiting with error:\s*(.+)$"]
    for line in raw_lines:
        message = line
        try:
            payload = json.loads(line)
            message = payload.get("msg", line)
        except Exception:
            pass
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                highlights.append(match.group(1).strip())
    deduped = []
    for item in highlights:
        item = item.replace("\\n", " ")
        item = re.sub(r'["}]+$', "", item).strip(" '\"")
        if item not in deduped:
            deduped.append(item)
    return deduped


def basic_auth_header():
    username = os.environ.get("LT_USERNAME", "")
    access_key = os.environ.get("LT_ACCESS_KEY", "")
    if not username or not access_key:
        return None
    encoded = base64.b64encode(f"{username}:{access_key}".encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {encoded}"}


def fetch_rca(stage, headers):
    params = {
        "taskId": stage.get("task_id_override", ""),
        "order": stage.get("order"),
        "iteration": stage.get("iteration", 0),
    }
    if not params["taskId"] or params["order"] is None:
        return []
    url = "https://api.hyperexecute.cloud/v1.0/categorizederrors?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("data", [])


def download_artifact_bundle(job_id, artifact_name, headers, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    query = urllib.parse.urlencode({"name": artifact_name})
    url = f"https://api.hyperexecute.cloud/v2.0/artefacts/{job_id}/download?{query}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    destination = output_dir / f"{artifact_name}.zip"
    with urllib.request.urlopen(request, timeout=60) as response:
        destination.write_bytes(response.read())
    return destination


def collect_rca_entries(result, headers):
    entries = []
    if not result or not headers:
        return entries
    for task in result.get("tasks", []):
        task_id = task.get("id")
        for stage in task.get("stages", []):
            if stage.get("status") != "failed":
                continue
            stage["task_id_override"] = task_id
            try:
                data = fetch_rca(stage, headers)
            except Exception:
                data = []
            entries.extend(data)
    return entries


def main():
    args = parse_args()
    result = load_result(args.result_json)
    failures = junit_failures(args.junit_dir)
    cli_notes = cli_highlights(args.cli_log)
    headers = basic_auth_header()
    rca_entries = collect_rca_entries(result, headers)
    artifact_paths = []
    artifact_output_dir = Path("reports/api-artifacts")
    job_id = result.get("id")
    if headers and job_id:
        for artifact_name in ["playwright-test-reports", "hyperexecute-runtime"]:
            try:
                artifact_paths.append(str(download_artifact_bundle(job_id, artifact_name, headers, artifact_output_dir)))
            except Exception:
                continue

    lines = [
        "# HyperExecute Failure Analysis",
        "",
        f"- Job ID: {result.get('id', 'unknown')}",
        f"- Job Status: {result.get('summary', {}).get('status', 'unknown')}",
        f"- Job Remark: {result.get('remark', 'n/a')}",
    ]

    job_link = result.get("summary", {}).get("job_link")
    if job_link:
        lines.append(f"- Job Link: {job_link}")

    if cli_notes:
        lines.extend(["", "## HyperExecute Signals", ""])
        lines.extend([f"- {note}" for note in cli_notes])

    if artifact_paths:
        lines.extend(["", "## Artifact Downloads Via API", ""])
        lines.extend([f"- Downloaded with API: {path}" for path in artifact_paths])

    if job_id:
        lines.extend(
            [
                "",
                "## Artifact Links",
                "",
                f"- Playwright reports: https://hyperexecute.lambdatest.com/artifact/view/{job_id}?artifactName=playwright-test-reports",
                f"- Runtime logs: https://hyperexecute.lambdatest.com/artifact/view/{job_id}?artifactName=hyperexecute-runtime",
            ]
        )

    lines.extend(["", "## Test Failure Breakdown", ""])
    if failures:
        for failure in failures:
            lines.append(f"- {failure['test']}: {failure['category']} - {failure['reason']}")
    else:
        lines.append("- No JUnit failures were found in the downloaded HyperExecute artifacts.")

    lines.extend(["", "## RCA From HyperExecute API", ""])
    if rca_entries:
        for entry in rca_entries:
            remediation = entry.get("remediation") or "No remediation text was returned."
            rca = entry.get("rca") or entry.get("error") or "No RCA text was returned."
            lines.append(f"- {entry.get('errorType', 'UnknownError')} in `{entry.get('filename', 'unknown')}` line {entry.get('lineNumber', 'n/a')}: {rca}")
            lines.append(f"  Suggested fix: {remediation}")
    else:
        lines.append("- No RCA entries were returned by the HyperExecute RCA API for this job.")

    lines.extend(["", "## Detailed Messages", ""])
    if failures:
        for failure in failures:
            lines.append(f"- {failure['test']}: {failure['message']}")
    else:
        lines.append("- None")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"failure_entries={len(failures)}")


if __name__ == "__main__":
    main()
