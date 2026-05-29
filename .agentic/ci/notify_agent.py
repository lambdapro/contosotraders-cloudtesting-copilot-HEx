import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result

REPORTS = Path("reports")


def _load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default if default is not None else {}


def _parse_junit(path):
    p = Path(path)
    if not p.exists():
        return {"tests": 0, "failures": 0, "errors": 0}
    try:
        tree = ET.parse(p)
        root = tree.getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        if suite is None:
            suite = root
        return {
            "tests":    int(suite.get("tests", 0)),
            "failures": int(suite.get("failures", 0)),
            "errors":   int(suite.get("errors", 0)),
        }
    except Exception:
        return {"tests": 0, "failures": 0, "errors": 0}


def build_payload() -> dict:
    run_id   = os.environ.get("GITHUB_RUN_ID", "")
    repo     = os.environ.get("GITHUB_REPOSITORY", "")
    run_url  = f"https://github.com/{repo}/actions/runs/{run_id}" if repo and run_id else ""

    rec  = _load_json(REPORTS / "release_recommendation.json")
    trac = _load_json(REPORTS / "traceability_matrix.json")
    fi   = _load_json(REPORTS / "failure_intelligence.json")
    sh   = _load_json(REPORTS / "self_healing_report.json")
    api  = _load_json(REPORTS / "api_details.json")
    qg   = _load_json(REPORTS / "quality_gates.json")
    conf = _load_json(REPORTS / "scenario-confidence-report.json")
    junit = _parse_junit(REPORTS / "junit.xml")

    trac_summary = trac.get("summary", {})
    he_summary   = api.get("he_summary", {})

    verdict      = rec.get("verdict", "UNKNOWN")
    pass_rate    = rec.get("pass_rate", trac_summary.get("pass_rate", 0))
    req_total    = rec.get("requirements_total", trac_summary.get("requirements_total", 0))
    req_covered  = rec.get("requirements_covered", trac_summary.get("requirements_covered", 0))

    executed = trac_summary.get("executed", 0)
    passed   = trac_summary.get("passed", 0)
    failed   = executed - passed if executed > passed else 0

    tests_total  = junit.get("tests", executed)
    tests_failed = junit.get("failures", 0) + junit.get("errors", 0)
    tests_passed = tests_total - tests_failed
    tests_flaky  = int(he_summary.get("flaky", 0))

    he_job_id = he_summary.get("job_id", "")
    he_dash   = (
        f"https://hyperexecute.lambdatest.com/task-queue/{he_job_id}"
        if he_job_id else ""
    )

    fi_failures  = fi.get("failures", [])
    top_failures = [
        {
            "scenario": f.get("failed_scenario", f.get("scenario_id", "")),
            "type":     f.get("failure_type", f.get("category", "UNKNOWN")),
            "message":  f.get("kane_one_liner", f.get("message", ""))[:120],
        }
        for f in fi_failures[:5]
    ]

    payload = {
        "run_id":               run_id,
        "run_url":              run_url,
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "verdict":              verdict,
        "pass_rate":            pass_rate,
        "requirements_total":   req_total,
        "requirements_covered": req_covered,
        "tests_total":          tests_total,
        "tests_passed":         tests_passed,
        "tests_failed":         tests_failed,
        "tests_flaky":          tests_flaky,
        "hyperexecute": {
            "job_id":    he_job_id,
            "status":    he_summary.get("status", ""),
            "passed":    int(he_summary.get("passed", 0)),
            "failed":    int(he_summary.get("failed", 0)),
            "flaky":     int(he_summary.get("flaky", 0)),
            "dashboard": he_dash,
        },
        "quality_gates": {
            "passed":           bool(qg.get("gates_passed", False)),
            "critical_failures": int(qg.get("critical_failures", 0)),
            "warnings":          int(qg.get("warnings", 0)),
        },
        "failure_intelligence": {
            "total_failures":  int(fi.get("total_failures", len(fi_failures))),
            "auto_remediable": int(fi.get("auto_remediable", 0)),
            "top_failures":    top_failures,
        },
        "self_healing": {
            "patches_applied":  int(sh.get("patches_applied", 0)),
            "rerun_scenarios":  sh.get("rerun_scenarios", []),
        },
        "confidence": conf.get("summary", {}).get("by_confidence_level", {}),
        "duration_s": 0,
        "report_links": {
            "github_actions":  run_url,
            "hyperexecute":    he_dash,
            "playwright_report": "",
        },
    }
    return payload


def write_summary(payload: dict) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return

    verdict  = payload.get("verdict", "UNKNOWN")
    pr       = payload.get("pass_rate", 0)
    tp       = payload.get("tests_passed", 0)
    tf       = payload.get("tests_failed", 0)
    he       = payload.get("hyperexecute", {})
    he_st    = he.get("status", "").upper() or "—"
    he_pass  = he.get("passed", 0)
    qg       = payload.get("quality_gates", {})
    qg_pass  = qg.get("passed", False)
    qg_crit  = qg.get("critical_failures", 0)
    qg_warn  = qg.get("warnings", 0)
    fi       = payload.get("failure_intelligence", {})
    fi_tot   = fi.get("total_failures", 0)
    fi_auto  = fi.get("auto_remediable", 0)
    sh_patches = payload.get("self_healing", {}).get("patches_applied", 0)

    qg_label = f"{'PASSED' if qg_pass else 'FAILED'} ({qg_crit} critical, {qg_warn} warnings)"

    lines = [
        f"## Pipeline Complete — {verdict}",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Verdict | **{verdict}** |",
        f"| Pass rate | {pr}% |",
        f"| Tests | {tp} passed / {tf} failed |",
        f"| HyperExecute | {he_st} ({he_pass} shards passed) |",
        f"| Quality gates | {qg_label} |",
        f"| Failures classified | {fi_tot} ({fi_auto} auto-remediable) |",
        f"| Self-healing patches | {sh_patches} applied |",
    ]

    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main():
    print_stage_header("N", "NOTIFY_AGENT", "Aggregate pipeline reports into execution_payload.json")
    REPORTS.mkdir(exist_ok=True)

    payload = build_payload()

    out_path = REPORTS / "execution_payload.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    write_summary(payload)

    print_stage_result("N", "NOTIFY_AGENT", {
        "Verdict":         payload.get("verdict", "UNKNOWN"),
        "Pass rate":       f"{payload.get('pass_rate', 0)}%",
        "Tests passed":    payload.get("tests_passed", 0),
        "Tests failed":    payload.get("tests_failed", 0),
        "HE job":          payload.get("hyperexecute", {}).get("job_id", "—"),
        "Failures total":  payload.get("failure_intelligence", {}).get("total_failures", 0),
        "SH patches":      payload.get("self_healing", {}).get("patches_applied", 0),
        "Output":          str(out_path),
    })


if __name__ == "__main__":
    main()
