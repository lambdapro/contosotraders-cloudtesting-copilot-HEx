"""
Stage 3 — Export Kane AI code-exports into the regression test folder.

Kane AI runs with `--code-export --code-language javascript`, which writes the
generated Playwright test for each session to its code-export directory
(~/.testmuai/kaneai/sessions/<session_id>/code-export/). This script copies those
JS files into src/ContosoTraders.Ui.Website/regression/<requirement>.test.js so the
HyperExecute matrix (test_files: ["regression/*.test.js"]) can run them.

Source of truth: requirements/analyzed_requirements.json — each item carries
kane_code_export_dir and/or kane_session_id.

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


def _pick_js(export_dir: Path) -> Path | None:
    """Choose the exported JS spec from a Kane code-export directory."""
    js = sorted(export_dir.rglob("*.js"))
    if not js:
        return None
    # Prefer a file that looks like a test/spec; else the largest .js
    for p in js:
        n = p.name.lower()
        if "test" in n or "spec" in n:
            return p
    return max(js, key=lambda p: p.stat().st_size)


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
    exported, missing = 0, []
    for item in data:
        rid = item.get("id", "AC")
        export_dir = _code_export_dir(item)
        if not export_dir:
            missing.append(rid)
            continue
        src = _pick_js(export_dir)
        if not src:
            missing.append(rid)
            continue
        dest = REGRESSION_DIR / f"{rid}_{_slug(item.get('title',''), rid)}.test.js"
        shutil.copyfile(src, dest)
        print(f"[export_kane_regression] {rid}: {src} -> {dest}")
        exported += 1

    print(f"[export_kane_regression] exported {exported} spec(s) to {REGRESSION_DIR}"
          + (f"; no code-export for: {', '.join(missing)}" if missing else ""))
    if exported == 0:
        print("[export_kane_regression] NOTE: no Kane JS code-exports were found "
              "(sessions dir not present on this runner, or Kane was skipped/cached). "
              "regression/ left as-is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
