"""
Stage 4b — Populate the HyperExecute matrix `files` from the Kane testmu exports.

HyperExecute matrix values must be a static string array, so the dynamic set of
Kane-exported Python scripts can't be expressed inline. This rewrites the
`files:` block of hyperexecute.yaml with the actual regression specs
(src/ContosoTraders.Ui.Website/regression/*.py) just before HyperExecute submits.
Each becomes one matrix task → `testSuites: python $files`.

Run from the repo root:
    python ci/build_he_matrix.py
Env overrides:
    HE_YAML        (default hyperexecute.yaml)
    HE_FILES_GLOB  (default src/ContosoTraders.Ui.Website/regression/*.py)
"""
import glob
import os
import re
from pathlib import Path

YAML       = os.environ.get("HE_YAML", "hyperexecute.yaml")
FILES_GLOB = os.environ.get("HE_FILES_GLOB", "src/ContosoTraders.Ui.Website/regression/*.py")


def discover() -> list[str]:
    # Paths relative to the repo root (testSuites runs `python $files` from there).
    return sorted(p.replace("\\", "/") for p in glob.glob(FILES_GLOB))


def rewrite_files(yaml_text: str, files: list[str]) -> str:
    lines = yaml_text.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    replaced = False
    while i < n:
        if re.match(r"^  files:\s*$", lines[i]):
            out.append("  files:")
            out.extend(f"    - {f}" for f in files)
            i += 1
            while i < n and lines[i].startswith("    "):   # drop old entries
                i += 1
            replaced = True
            continue
        out.append(lines[i])
        i += 1
    if not replaced:
        raise SystemExit("[build_he_matrix] ERROR: no '  files:' block found in " + YAML)
    return "\n".join(out) + "\n"


def main() -> None:
    files = discover()
    if not files:
        print(f"[build_he_matrix] WARNING: no specs matched {FILES_GLOB} — leaving files unchanged")
        return
    path = Path(YAML)
    if not path.exists():
        raise SystemExit(f"[build_he_matrix] ERROR: {YAML} not found (run from repo root)")
    path.write_text(rewrite_files(path.read_text(encoding="utf-8"), files), encoding="utf-8")
    print(f"[build_he_matrix] matrix.files set to {len(files)} spec(s):")
    for f in files:
        print(f"    - {f}")


if __name__ == "__main__":
    main()
