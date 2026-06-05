"""
Stage 3 — Export Kane AI code-exports into the regression test folder.

Kane AI runs with `--code-export`, writing a Python `testmu` script (test.py) for
each session to ~/.testmuai/kaneai/sessions/<session_id>/code-export/. (kane-cli
only emits Python — `--code-language javascript` is a no-op as of 0.4.0.) Each
script is run with `python <file>` via the testmu runtime: it launches a local
Playwright browser and calls Kane's AI vision API for assertions.

This copies each session's test.py into
src/ContosoTraders.Ui.Website/regression/<requirement>.py so the HyperExecute
matrix (`testSuites: python $files`) can run them.

Source of truth: requirements/analyzed_requirements.json (kane_code_export_dir /
kane_session_id per item).

Run from the repo root:
    python ci/export_kane_regression.py
Env overrides:
    KANE_ANALYZED   (default requirements/analyzed_requirements.json)
    REGRESSION_DIR  (default src/ContosoTraders.Ui.Website/regression)
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

ANALYZED       = os.environ.get("KANE_ANALYZED", "requirements/analyzed_requirements.json")
REGRESSION_DIR = Path(os.environ.get("REGRESSION_DIR", "src/ContosoTraders.Ui.Website/regression"))
KANE_SESSIONS  = Path.home() / ".testmuai" / "kaneai" / "sessions"


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return (s or fallback)[:48]


def _code_export_dir(item: dict) -> Path | None:
    raw = item.get("kane_code_export_dir") or ""
    if raw and Path(raw).is_dir():
        return Path(raw)
    sid = item.get("kane_session_id") or ""
    if sid:
        cand = KANE_SESSIONS / sid / "code-export"
        if cand.is_dir():
            return cand
    return None


def _pick_py(export_dir: Path) -> Path | None:
    """Choose the exported testmu Python script from a Kane code-export dir."""
    py = sorted(export_dir.rglob("*.py"))
    if not py:
        return None
    for p in py:                       # prefer the canonical test.py
        if p.name.lower() == "test.py":
            return p
    for p in py:
        if "test" in p.name.lower():
            return p
    return max(py, key=lambda p: p.stat().st_size)


def main() -> int:
    data = []
    p = Path(ANALYZED)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[export_kane_regression] WARN: cannot parse {ANALYZED}: {exc}")
    if isinstance(data, dict):
        data = data.get("requirements") or data.get("data") or []

    REGRESSION_DIR.mkdir(parents=True, exist_ok=True)
    exported, no_dir, no_py = 0, [], []
    for item in data:
        rid = item.get("id", "AC")
        export_dir = _code_export_dir(item)
        if not export_dir:
            no_dir.append(rid)
            continue
        src = _pick_py(export_dir)
        if not src:
            no_py.append(f"{rid}({', '.join(x.name for x in export_dir.iterdir())})")
            continue
        dest = REGRESSION_DIR / f"{rid}_{_slug(item.get('title',''), rid)}.py"
        shutil.copyfile(src, dest)
        print(f"[export_kane_regression] {rid}: {src} -> {dest}")
        exported += 1

    print(f"[export_kane_regression] exported {exported} testmu spec(s) to {REGRESSION_DIR}")
    if no_dir:
        print(f"[export_kane_regression]   no code-export dir (Kane failed/no export): {', '.join(no_dir)}")
    if no_py:
        print(f"[export_kane_regression]   dir present but no .py found: {', '.join(no_py)}")
    if exported == 0:
        print("[export_kane_regression] NOTE: no testmu exports copied — Kane sessions dir "
              "not present on this runner, or Kane was skipped/cached.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
