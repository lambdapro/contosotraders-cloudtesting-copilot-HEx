"""
Pipeline metrics collector.
Reads partial metrics written by individual stages and produces a
consolidated pipeline_metrics.json with timing, cache stats, and verdict.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def collect():
    metrics_path = Path("reports/pipeline_metrics.json")
    metrics = _load(metrics_path)

    stages = metrics.get("stages", {})

    # Compute totals
    total_duration = sum(
        s.get("duration_seconds", 0) for s in stages.values()
    )
    cache_hits = sum(1 for s in stages.values() if s.get("cache_hit"))
    cache_misses = sum(1 for s in stages.values() if not s.get("cache_hit"))

    # Pull verdict from release recommendation if available
    verdict = "UNKNOWN"
    rec_path = Path("reports/release_recommendation.md")
    if rec_path.exists():
        content = rec_path.read_text(encoding="utf-8")
        for token in ("GREEN", "YELLOW", "RED"):
            if token in content:
                verdict = token
                break

    # Pull pass/fail counts from traceability JSON if available
    passed = failed = total_tests = 0
    trace_path = Path("reports/traceability_matrix.json")
    if trace_path.exists():
        trace = _load(trace_path)
        rows = trace.get("rows", trace) if isinstance(trace, dict) else trace
        if isinstance(rows, list):
            total_tests = len(rows)
            passed = sum(1 for r in rows if r.get("overall", "").upper() == "PASSED")
            failed = total_tests - passed

    summary = {
        "pipeline_complete": datetime.now(timezone.utc).isoformat(),
        "total_duration_seconds": round(total_duration, 2),
        "verdict": verdict,
        "tests_total": total_tests,
        "tests_passed": passed,
        "tests_failed": failed,
        "pass_rate_pct": round((passed / total_tests * 100) if total_tests else 0, 1),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "demo_mode": metrics.get("demo_mode", "false"),
        "requirements_hash": metrics.get("requirements_hash", ""),
        "stages": stages,
    }

    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("Pipeline Metrics Summary")
    print("=" * 50)
    print(f"  Total duration : {total_duration:.1f}s ({total_duration/60:.1f} min)")
    print(f"  Verdict        : {verdict}")
    print(f"  Tests passed   : {passed}/{total_tests}")
    print(f"  Cache hits     : {cache_hits} | misses: {cache_misses}")
    print(f"  Demo mode      : {metrics.get('demo_mode', 'false')}")
    print()
    print("Stage breakdown:")
    for stage_name, stage_data in stages.items():
        hit = "CACHE HIT" if stage_data.get("cache_hit") else "live"
        print(f"  {stage_name:<25} {stage_data.get('duration_seconds', 0):>6.1f}s  [{hit}]")

    return summary


if __name__ == "__main__":
    collect()
