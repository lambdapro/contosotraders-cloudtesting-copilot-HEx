"""
Normalize all execution artifacts into reports/normalized_results.json.

Priority order per scenario+browser combination:
  1. reports/kane_result_SC-*_<browser>.json  (conftest — real timing, real status)
  2. reports/junit-<browser>.xml / reports/junit.xml  (pytest JUnit — real pass/fail, real duration)
  3. reports/api_details.json he_tasks  (HE API — real session links)

When data is missing: status = "data_unavailable". Never fabricates values.

Writes: reports/normalized_results.json
"""
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result

DEBUG = os.environ.get("REPORT_DEBUG", "false").lower() == "true"


def _debug(msg):
    if DEBUG:
        print(f"[REPORT_DEBUG] {msg}")


def _load_json(path, default):
    p = Path(path)
    if not p.exists():
        _debug(f"File not found: {path}")
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        _debug(f"Failed to parse {path}: {exc}")
        return default


def _load_scenarios():
    scenarios = _load_json("scenarios/scenarios.json", [])
    return {s["id"]: s for s in scenarios}


def _load_conftest_results():
    """Load all kane_result_SC-*_<browser>.json files written by conftest during test execution."""
    results = {}
    reports_dir = Path("reports")
    if not reports_dir.exists():
        return results
    for f in sorted(reports_dir.glob("kane_result_SC-*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sc_id = data.get("scenario_id", "")
            browser = data.get("browser", "chrome")
            key = (sc_id, browser)
            results[key] = data
            _debug(f"conftest result: {f.name} → {sc_id}/{browser} status={data.get('status')}")
        except Exception as exc:
            _debug(f"Failed to parse {f}: {exc}")
    return results


def _load_junit_results():
    """
    Load junit-<browser>.xml and junit.xml files.
    Returns dict: (test_name, browser) → {status, duration_ms, error_message, source}.
    """
    results = {}
    reports_dir = Path("reports")
    if not reports_dir.exists():
        return results

    xml_files = list(reports_dir.glob("junit-*.xml")) + list(reports_dir.glob("junit.xml"))
    for xml_file in xml_files:
        stem = xml_file.stem  # "junit-chrome" or "junit"
        browser = stem.replace("junit-", "") if stem.startswith("junit-") else "chrome"

        try:
            root = ET.fromstring(xml_file.read_text(encoding="utf-8"))
        except Exception as exc:
            _debug(f"Failed to parse {xml_file}: {exc}")
            continue

        for testcase in root.iter("testcase"):
            tc_name = testcase.attrib.get("name", "")
            duration_s = float(testcase.attrib.get("time", "0") or "0")
            duration_ms = round(duration_s * 1000)

            failure_el = testcase.find("failure")
            error_el = testcase.find("error")
            skipped_el = testcase.find("skipped")

            if failure_el is not None or error_el is not None:
                status = "failed"
                el = failure_el if failure_el is not None else error_el
                error_msg = (el.attrib.get("message", "") or el.text or "")[:500]
            elif skipped_el is not None:
                status = "skipped"
                error_msg = None
            else:
                status = "passed"
                error_msg = None

            key = (tc_name, browser)
            results[key] = {
                "status": status,
                "duration_ms": duration_ms,
                "error_message": error_msg,
                "source": "junit",
            }
            _debug(f"junit: {tc_name}/{browser} → {status} ({duration_ms}ms)")

    return results


def _sc_id_from_task_name(name: str) -> str:
    """Extract SC-NNN from a HE task name like 'tests/.../test_sc_003_...'."""
    import re
    m = re.search(r"test_sc_(\d+)", name, re.IGNORECASE)
    if m:
        return f"SC-{int(m.group(1)):03d}"
    return ""


def _load_he_session_links():
    """
    Load HE API task data for session links.
    Returns dict: sc_id → list of {session_link, status, name}.
    """
    api_details = _load_json("reports/api_details.json", {})
    result: dict = {}
    seen_links: set = set()
    for task in api_details.get("he_tasks", []):
        name = (task.get("name") or "").strip()
        link = task.get("session_link", "")
        if not name:
            continue
        sc_id = _sc_id_from_task_name(name)
        if not sc_id:
            _debug(f"HE API task: could not parse SC ID from {name!r}")
            continue
        if link in seen_links:
            continue
        seen_links.add(link)
        result.setdefault(sc_id, []).append({
            "session_link": link,
            "status": task.get("status", ""),
            "name": name,
        })
        _debug(f"HE API task: {sc_id} → {task.get('status')} link={link[:60]}")
    return result


def _fn_matches(junit_name: str, fn_name: str) -> bool:
    """True if junit_name ends with fn_name or contains it (handles class::method format)."""
    return fn_name and (junit_name == fn_name or junit_name.endswith(f"::{fn_name}") or junit_name.endswith(fn_name))


def normalize():
    print_stage_header("6a", "NORMALIZE_ARTIFACTS", "Consolidate conftest, JUnit, and HE API results")
    scenarios_by_id = _load_scenarios()
    conftest_results = _load_conftest_results()
    junit_results = _load_junit_results()
    he_sessions = _load_he_session_links()

    _debug(f"Scenarios loaded: {len(scenarios_by_id)}")
    _debug(f"Conftest results: {len(conftest_results)}")
    _debug(f"JUnit results: {len(junit_results)}")
    _debug(f"HE session links: {len(he_sessions)}")

    normalized = []
    missing_data = []

    for sc_id, scenario in scenarios_by_id.items():
        if scenario.get("status") == "deprecated":
            continue

        req_id = scenario.get("requirement_id", "")
        tc_id = scenario.get("test_case_id", "")
        fn_name = scenario.get("function_name", f"test_{sc_id.lower().replace('-', '_')}")

        # Determine which browsers have results for this scenario
        browsers_from_conftest = {b for (sid, b) in conftest_results if sid == sc_id}
        browsers_from_junit = {b for (jname, b) in junit_results if _fn_matches(jname, fn_name)}
        browsers = browsers_from_conftest | browsers_from_junit

        # HE sessions for this scenario (list, keyed by sc_id now)
        he_sessions_for_sc = he_sessions.get(sc_id, [])

        if not browsers:
            if he_sessions_for_sc:
                # HE has data but no browser discriminator — emit one record per HE session
                # Map sessions to browser names using BROWSERS env var order if available
                _default_browsers = ["chrome", "firefox", "safari", "android"]
                configured_browsers = [
                    b.strip().lower()
                    for b in os.environ.get("BROWSERS", "").split(",")
                    if b.strip()
                ] or _default_browsers
                for idx, he_sess in enumerate(he_sessions_for_sc):
                    browser = configured_browsers[idx] if idx < len(configured_browsers) else f"browser_{idx + 1}"
                    he_status = he_sess.get("status", "")
                    status = he_status if he_status in ("passed", "failed") else "data_unavailable"
                    record = {
                        "test_id": tc_id,
                        "requirement_id": req_id,
                        "scenario_id": sc_id,
                        "function_name": fn_name,
                        "browser": browser,
                        "framework": "playwright",
                        "status": status,
                        "duration_ms": None,
                        "start_time": None,
                        "end_time": None,
                        "retries": 0,
                        "session_link": he_sess.get("session_link", ""),
                        "error_message": None,
                        "source": "he_api",
                    }
                    normalized.append(record)
                    _debug(f"Normalized (HE only): {sc_id}/{browser} → {status}")
            else:
                missing_data.append(f"{sc_id}: no execution data in any source")
                normalized.append(_unavailable_record(sc_id, req_id, tc_id, fn_name, "chrome"))
            continue

        for browser in sorted(browsers):
            conftest = conftest_results.get((sc_id, browser), {})

            # JUnit: find matching entry by function name + browser
            junit = next(
                (jdata for (jname, jbr), jdata in junit_results.items()
                 if jbr == browser and _fn_matches(jname, fn_name)),
                None,
            )

            # HE API: pick first session for session link (no browser discriminator available)
            he = he_sessions_for_sc[0] if he_sessions_for_sc else {}

            # Status: conftest > junit > he_api (he_api status is less granular)
            status = (
                conftest.get("status")
                or (junit or {}).get("status")
                or (he.get("status") if he.get("status") in ("passed", "failed") else None)
            )
            if status is None:
                missing_data.append(f"{sc_id}/{browser}: no execution status found")
                status = "data_unavailable"

            duration_ms = conftest.get("duration_ms") or (junit or {}).get("duration_ms")
            session_link = he.get("session_link", "")
            error_message = conftest.get("error_message") or (junit or {}).get("error_message")
            start_time = conftest.get("start_time")
            end_time = conftest.get("end_time")

            if conftest:
                source = "conftest"
            elif junit:
                source = "junit"
            elif he:
                source = "he_api"
            else:
                source = "none"

            record = {
                "test_id": tc_id,
                "requirement_id": req_id,
                "scenario_id": sc_id,
                "function_name": fn_name,
                "browser": browser,
                "framework": "playwright",
                "status": status,
                "duration_ms": duration_ms,
                "start_time": start_time,
                "end_time": end_time,
                "retries": 0,
                "session_link": session_link,
                "error_message": error_message,
                "source": source,
            }
            normalized.append(record)
            _debug(f"Normalized: {sc_id}/{browser} → {status} (source={source}, dur={duration_ms}ms)")

    for msg in missing_data:
        print(f"[normalize] WARNING: {msg}")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": normalized,
        "missing_data": missing_data,
    }

    Path("reports").mkdir(parents=True, exist_ok=True)
    Path("reports/normalized_results.json").write_text(
        json.dumps(out, indent=2) + "\n", encoding="utf-8"
    )

    total = len(normalized)
    passed = sum(1 for r in normalized if r["status"] == "passed")
    failed = sum(1 for r in normalized if r["status"] == "failed")
    unavailable = sum(1 for r in normalized if r["status"] == "data_unavailable")
    sources = {r.get("source", "none") for r in normalized}

    print_stage_result("6a", "NORMALIZE_ARTIFACTS", {
        "Results normalized":  total,
        "Passed":              passed,
        "Failed":              failed,
        "Data unavailable":    unavailable,
        "Sources used":        ", ".join(sorted(sources)),
        "Output":              "reports/normalized_results.json",
    })
    return out


def _unavailable_record(sc_id, req_id, tc_id, fn_name, browser):
    return {
        "test_id": tc_id,
        "requirement_id": req_id,
        "scenario_id": sc_id,
        "function_name": fn_name,
        "browser": browser,
        "framework": "playwright",
        "status": "data_unavailable",
        "duration_ms": None,
        "start_time": None,
        "end_time": None,
        "retries": 0,
        "session_link": "",
        "error_message": None,
        "source": "none",
    }


if __name__ == "__main__":
    normalize()
