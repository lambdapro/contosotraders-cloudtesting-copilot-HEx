import argparse
import subprocess
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("nodeid")
    return parser.parse_args()


def safe_name(nodeid):
    return (
        nodeid.replace("::", "__")
        .replace("/", "_")
    )


def main():
    args = parse_args()
    reports_dir = Path("reports")
    junit_dir = reports_dir / "junit"
    html_dir = reports_dir / "html"
    junit_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)

    name = safe_name(args.nodeid)
    command = [
        "pytest",
        args.nodeid,
        "-v",
        "--tb=short",
        f"--junitxml={junit_dir / f'{name}.xml'}",
        f"--html={html_dir / f'{name}.html'}",
        "--self-contained-html",
    ]
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
