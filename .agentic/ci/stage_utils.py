"""Shared stage result printing utility for Agentic STLC pipeline."""

import sys
from datetime import datetime, timezone


def print_stage_result(
    stage_num: str,
    stage_name: str,
    details: dict,
    success: bool = True,
) -> None:
    """Print structured stage completion summary to stdout."""
    status = "complete" if success else "FAILED"
    icon = "✅" if success else "❌"
    width = 62
    print(f"\n{'=' * width}")
    print(f"  {icon}  [Stage {stage_num}] {stage_name} — {status}")
    print(f"{'=' * width}")
    for key, value in details.items():
        print(f"  {key:<26}: {value}")
    print(f"  {'Completed at':<26}: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    print(f"{'=' * width}\n")
    sys.stdout.flush()


def print_stage_header(stage_num: str, stage_name: str, description: str = "") -> None:
    """Print stage start banner."""
    width = 62
    print(f"\n{'─' * width}")
    print(f"  ▶  [Stage {stage_num}] {stage_name}")
    if description:
        print(f"     {description}")
    print(f"{'─' * width}")
    sys.stdout.flush()
