import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result

# On GitHub Actions: /home/runner/.testmuai/kaneai/sessions/
# On Windows local:  C:/Users/<user>/.testmuai/kaneai/sessions/
KANE_SESSIONS_DIR = Path.home() / ".testmuai" / "kaneai" / "sessions"
_KANE_PROJECT_CONFIGURED = False


def _parse_file_url(raw: str) -> str:
    """Convert a file:// URL (from Kane's CodeExport link) to an OS path.

    Handles both Linux  (file:///home/runner/...) and Windows
    (file:///C:/Users/...) formats that appear in Kane CLI terminal output.
    """
    token = raw.strip()
    if not token.lower().startswith("file://"):
        return token  # already a plain path
    # Strip the scheme — leaves ///home/... or ///C:/...
    no_scheme = token[7:]           # e.g.  /home/runner/... or /C:/Users/...
    if sys.platform == "win32":
        # file:///C:/path → /C:/path → strip leading slash → C:/path
        if no_scheme.startswith("/") and len(no_scheme) > 2 and no_scheme[2] == ":":
            no_scheme = no_scheme[1:]
    return no_scheme


def _resolve_code_export_path(raw_path: str) -> str:
    """Given a path that may point to a file or a directory, return the
    parent code-export directory only if it contains .py files."""
    p = Path(raw_path)
    # If it's already a directory, use it directly
    candidates = [p, p.parent]
    for c in candidates:
        if c.is_dir() and any(c.glob("*.py")):
            return str(c)
    return ""


def _find_code_export_by_session_id(session_id: str) -> str:
    """Construct and verify the code-export path from a known Kane session ID.

    This is the authoritative lookup on GitHub Actions where session IDs are
    available via NDJSON and the sessions directory is at a fixed location.
    The path is deterministic: KANE_SESSIONS_DIR/<session_id>/code-export/
    """
    if not session_id:
        return ""
    candidate = KANE_SESSIONS_DIR / session_id / "code-export"
    if candidate.is_dir() and any(candidate.glob("*.py")):
        return str(candidate)
    return ""


import re as _re
_UUID_RE = _re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    _re.IGNORECASE,
)
# Kane's plain-text links box uses keyword labels before the URL
_LINK_LABEL_RE = _re.compile(
    r"(sharelink|testcase|sessionlink|recordinglink|session[-_\s]?url)\s+",
    _re.IGNORECASE,
)
_HTTP_URL_RE = _re.compile(r"https?://\S+")


def _parse_kane_output(combined: str) -> dict:
    """
    Parse Kane CLI stdout+stderr and return a result dict with all extracted fields:
      status, summary, one_liner, steps, final_state, duration,
      test_url, session_id, code_export_dir, share_link, testcase_link

    Handles:
      - NDJSON event stream (step_end, run_end, code_export, …)
      - Plain-text links box printed at session end:
          │  ShareLink    https://share.testmuai.com/...  │
          │  TestCase     https://test-manager.testmuai.com/...  │
          │  CodeExport   file:///...  │
      - Bare text lines without box borders
    """
    run_end = None
    step_summaries: list[str] = []
    session_id = ""
    code_export_dir = ""
    share_link = ""
    testcase_link = ""

    for raw in combined.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue

        # ── Attempt JSON parse ──────────────────────────────────────────────
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            event = None

        if event is not None:
            etype = event.get("type", "")
            if etype in ("step_end", "stepEnd") and event.get("summary"):
                step_summaries.append(event["summary"])
            elif etype in ("run_end", "runEnd"):
                run_end = event
                session_id = session_id or (
                    event.get("session_id")
                    or event.get("sessionId")
                    or event.get("data", {}).get("session_id", "")
                    or ""
                )
            elif etype in ("code_export", "codeExport"):
                raw_path = event.get("path") or event.get("directory") or ""
                if raw_path:
                    code_export_dir = code_export_dir or _resolve_code_export_path(raw_path)
            elif etype in ("share_link", "shareLink"):
                share_link = share_link or event.get("url", "")
            elif etype in ("test_case", "testCase"):
                testcase_link = testcase_link or event.get("url", "")

            if not session_id:
                session_id = event.get("session_id") or event.get("sessionId") or ""

            raw_export = event.get("code_export_path", "") or event.get("export_path", "")
            if raw_export and not code_export_dir:
                code_export_dir = _resolve_code_export_path(
                    _parse_file_url(raw_export)
                )
            continue

        # ── Plain-text line parsing ─────────────────────────────────────────
        upper = stripped.upper().replace(" ", "").replace("-", "")

        # CodeExport link
        if "CODEEXPORT" in upper:
            for token in stripped.split():
                if token.lower().startswith("file://"):
                    resolved = _resolve_code_export_path(_parse_file_url(token))
                    code_export_dir = code_export_dir or resolved
                    break
            if not code_export_dir:
                for token in stripped.split():
                    if "code-export" in token.lower() or "kaneai/sessions" in token.lower():
                        resolved = _resolve_code_export_path(token)
                        code_export_dir = code_export_dir or resolved
                        break

        # ShareLink / TestCase / session links — Kane prints these as:
        #   "ShareLink   https://share.testmuai.com/..."
        #   "│  ShareLink    https://...  │"
        if "SHARELINK" in upper or "SHARE.TESTMUAI" in upper:
            m = _HTTP_URL_RE.search(stripped)
            if m:
                share_link = share_link or m.group(0).rstrip("│ \t")

        if "TESTCASE" in upper or "TEST-MANAGER.TESTMUAI" in upper or "TESTMANAGER" in upper:
            m = _HTTP_URL_RE.search(stripped)
            if m:
                testcase_link = testcase_link or m.group(0).rstrip("│ \t")

        # SessionLink / recording link
        if ("SESSIONLINK" in upper or "RECORDINGLINK" in upper) and not share_link:
            m = _HTTP_URL_RE.search(stripped)
            if m:
                share_link = m.group(0).rstrip("│ \t")

        # Session UUID from any line that mentions sessions dir
        if not session_id and "sessions" in stripped.lower():
            m = _UUID_RE.search(stripped)
            if m:
                session_id = m.group(0)

    # Fallback: derive code-export path from session ID
    if not code_export_dir and session_id:
        code_export_dir = _find_code_export_by_session_id(session_id)

    # Derive test_url from run_end or share_link
    test_url = ""
    if run_end:
        test_url = run_end.get("test_url", "") or run_end.get("session_url", "")
    if not test_url and share_link:
        test_url = share_link
    if not test_url and session_id:
        test_url = f"https://test-manager.lambdatest.com/session/{session_id}"

    return {
        "run_end": run_end,
        "step_summaries": step_summaries,
        "session_id": session_id,
        "code_export_dir": code_export_dir,
        "share_link": share_link,
        "testcase_link": testcase_link,
        "test_url": test_url,
    }


def _kane_exe():
    """Return the kane-cli executable, resolving .cmd wrapper on Windows."""
    exe = shutil.which("kane-cli")
    if exe is None and sys.platform == "win32":
        exe = shutil.which("kane-cli.cmd")
    return exe or "kane-cli"


KANE_EXE = _kane_exe()

TARGET_URL = os.environ.get("TARGET_URL", "https://ecommerce-playground.lambdatest.io/")


def _configure_kane_project():
    """Configure Kane CLI Test Manager project and folder once per process."""
    global _KANE_PROJECT_CONFIGURED
    if _KANE_PROJECT_CONFIGURED:
        return
    project_id = os.environ.get("KANE_PROJECT_ID", "")
    folder_id = os.environ.get("KANE_FOLDER_ID", "")
    if project_id:
        subprocess.run([KANE_EXE, "config", "project", project_id],
                       capture_output=True, text=True, check=False)
        print(f"[Stage 1] Kane project configured: {project_id}")
    if folder_id:
        subprocess.run([KANE_EXE, "config", "folder", folder_id],
                       capture_output=True, text=True, check=False)
        print(f"[Stage 1] Kane folder configured: {folder_id}")
    _KANE_PROJECT_CONFIGURED = True




def build_name():
    """Consistent build label shared by KaneAI and Playwright sessions in the same run."""
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"Agentic STLC #{run_number} | {today}" if run_number else f"Agentic STLC | {today}"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", default="requirements")
    parser.add_argument("--output", default="requirements/analyzed_requirements.json")
    parser.add_argument("--kane-results", default="reports/kane_results.json")
    parser.add_argument("--skip-kane", action="store_true")
    parser.add_argument("--demo-mode", action="store_true",
                        help="Load pre-generated results from ci/demo_kane_results.json instead of calling Kane")
    return parser.parse_args()


def extract_acceptance_criteria(text):
    """Extracts acceptance criteria using deterministic line parsing.

    Handles both dense format (consecutive lines) and spaced format (blank
    lines between criteria). Strips optional 'AC-NNN:' / bullet prefixes.
    """
    criteria = []
    lines = [line.strip() for line in text.splitlines()]
    capture = False
    _AC_PREFIX = __import__("re").compile(r"^(AC-\d+|SC-\d+|\d+\.?)\s*[:.)]\s*", __import__("re").IGNORECASE)
    _STOP = ("---", "as a ", "i want to ", "so that ", "user story", "given ", "when ", "then ")

    for line in lines:
        if line.lower().strip().rstrip(":").startswith("acceptance criteria"):
            capture = True
            continue
        if not capture:
            continue
        # Stop on section headings (but not on blank lines — skip those)
        if not line:
            continue
        if line.startswith("---") or line.lower().startswith("title") or \
           any(line.lower().startswith(p) for p in _STOP):
            capture = False
            continue
        # Strip optional 'AC-001: ' or '1. ' prefix
        clean = _AC_PREFIX.sub("", line).strip()
        if clean:
            criteria.append(clean)
    return criteria


def make_title(description):
    words = description.replace(".", "").replace(":", "").split()
    return " ".join(words[:10]).strip().capitalize()


# Optimized Kane task overrides: precise, flow-aware, termination-explicit objectives
# Keys are canonical substrings from the acceptance criterion descriptions.
# Values are task strings passed directly to `kane-cli run`.
_KANE_TASK_OVERRIDES: dict[str, str] = {
    # SC-001: product_id=28 (HTC Touch HD) is Out of Stock — use Cameras category (path=33)
    # which has confirmed in-stock products with active Add to Cart buttons.
    "add a product to the cart from the product detail page": (
        "Go to https://ecommerce-playground.lambdatest.io/index.php?route=product/category&path=33"
        " — click the first product thumbnail in the listing"
        " — on the product page click the Add to Cart button (it must be an active blue button, not an Out Of Stock button;"
        " if Out Of Stock go back and try the next product)"
        " — verify the cart icon in the top navigation shows a count of 1 or more."
        " Stop immediately once the cart count is updated. Do not navigate further."
    ),
    # SC-002: same OOS fix — use Cameras category first product
    "open the cart dropdown and see the list of added items": (
        "Go to https://ecommerce-playground.lambdatest.io/index.php?route=product/category&path=33"
        " — click the first product thumbnail — on the product page click the Add to Cart button"
        " (active button, not Out Of Stock)"
        " — then click the cart icon in the top-right header to expand the mini-cart dropdown"
        " — verify at least one item name and price appears in the dropdown."
        " Stop once the item name and price are visible in the cart dropdown."
    ),
    # SC-003: generic task produced raw recording_state JSON — Kane never ran the objective.
    # Direct URL to Laptops category forces correct landing and immediate verification.
    "navigate to the laptops product catalog": (
        "Navigate directly to https://ecommerce-playground.lambdatest.io/index.php?route=product/category&path=18"
        " — wait for the page to fully load — verify a grid of product thumbnails is visible"
        " (at least one product card with an image and price)."
        " Stop immediately once the product grid is confirmed. Do not navigate anywhere else."
    ),

    # SC-004: was unclassified (no override existed). Direct URL to Components category avoids
    # the wrong-site navigation bug where Kane opened kaneai-playground instead.
    "apply a manufacturer brand filter from the sidebar": (
        "Go directly to https://ecommerce-playground.lambdatest.io/index.php?route=product/category&path=25"
        " — wait for the page to fully load — in the left sidebar locate the MANUFACTURER filter section"
        " — click the checkbox or link next to Apple"
        " — wait for the product grid to refresh — verify the product listing has updated"
        " (fewer results shown or the URL now includes a manufacturer parameter)."
        " Stop once the filtered product list is confirmed. Do not navigate to any other site."
    ),
    # SC-009: 'valid credentials' was too vague — Kane used non-existent emails and drifted to
    # registration. Register a fixed test account first; subsequent runs log in with those creds.
    "log in with a registered email address and password and land on the account dashboard": (
        "Go to https://ecommerce-playground.lambdatest.io/index.php?route=account/login"
        " — try logging in with Email=stlctest@example.com and Password=Test@12345"
        " — if the credentials are invalid (error message appears), navigate to"
        " https://ecommerce-playground.lambdatest.io/index.php?route=account/register"
        " and register: First Name=STLC, Last Name=Test, Email=stlctest@example.com,"
        " Telephone=5550001234, Password=Test@12345, Confirm Password=Test@12345"
        " — accept Privacy Policy — click Continue — on the success page click Continue"
        " — verify the account dashboard page loads (URL contains route=account/account)."
        " Stop immediately once the dashboard heading is visible. Do not explore further."
    ),
    # SC-010: same credential fix — register if needed, then log out
    "log out from the account and be redirected to the home page": (
        "Go to https://ecommerce-playground.lambdatest.io/index.php?route=account/login"
        " — log in with Email=stlctest@example.com, Password=Test@12345"
        " (if invalid, first register at /index.php?route=account/register with those credentials)"
        " — once on the account dashboard click My Account in the top navigation bar"
        " — click Logout from the dropdown menu"
        " — verify the page redirects to the home page (root URL, not an account page)."
        " Stop once the home page is confirmed."
    ),
    # SC-011: product_id=28 OOS — use Cameras category first in-stock product
    "remove an item from the shopping cart and see the cart update with the item gone": (
        "Go to https://ecommerce-playground.lambdatest.io/index.php?route=product/category&path=33"
        " — click the first product thumbnail — click the Add to Cart button (active, not Out Of Stock)"
        " — navigate directly to https://ecommerce-playground.lambdatest.io/index.php?route=checkout/cart"
        " — click the Remove button (x icon) next to the item in the cart"
        " — verify the cart page shows Your shopping cart is empty."
        " Stop immediately after the empty cart message is confirmed."
    ),
    # SC-012: product_id=28 OOS — use Cameras category first in-stock product
    "update the quantity of an item in the shopping cart and see the line total recalculate": (
        "Go to https://ecommerce-playground.lambdatest.io/index.php?route=product/category&path=33"
        " — click the first product thumbnail — click the Add to Cart button (active, not Out Of Stock)"
        " — navigate directly to https://ecommerce-playground.lambdatest.io/index.php?route=checkout/cart"
        " — change the quantity input field value to 2 — click the Update button"
        " — verify the line total price now reflects 2 items (doubled unit price)."
        " Stop once the recalculated total is visible."
    ),
    # SC-014: 'valid credentials' was vague and wishlist requires login. Use fixed test account.
    "add a product to the wish list from the product detail page and view it in the wishlist": (
        "Go to https://ecommerce-playground.lambdatest.io/index.php?route=account/login"
        " — log in with Email=stlctest@example.com, Password=Test@12345"
        " (if invalid, first register at /index.php?route=account/register with those credentials)"
        " — once logged in navigate to"
        " https://ecommerce-playground.lambdatest.io/index.php?route=product/product&product_id=40"
        " — click the Add to Wish List heart icon button"
        " — verify a success notification appears confirming the item was added to the wish list."
        " Stop once the wish list success message is shown."
    ),
    # SC-015: product_id=28 OOS — use Cameras category first in-stock product for checkout
    "complete a guest checkout by entering a shipping address and selecting flat rate shipping": (
        "Go to https://ecommerce-playground.lambdatest.io/index.php?route=product/category&path=33"
        " — click the first product thumbnail — click Add to Cart (active button, not Out Of Stock)"
        " — navigate to https://ecommerce-playground.lambdatest.io/index.php?route=checkout/checkout"
        " — select the Guest Checkout option"
        " — fill in the billing address form: First Name=Test, Last Name=User, Email=guest@test.com,"
        " Address 1=123 Main St, City=Austin, Post Code=78701, Country=United States, Region=Texas"
        " — select Flat Rate shipping"
        " — verify you can proceed past the shipping step (Continue button active or payment step visible)."
        " Stop once the shipping selection is confirmed."
    ),
}


def _get_kane_task(description: str) -> str:
    """Return an optimized Kane task or the generic fallback."""
    dl = description.lower()
    for keyword, task in _KANE_TASK_OVERRIDES.items():
        if keyword in dl:
            return task
    return f"On {TARGET_URL} — {description}"


EXIT_STATUS = {0: "passed", 1: "failed", 2: "error", 3: "timeout"}


def _run_kane_indexed(args):
    return run_kane(*args)


def run_kane(index, description):
    username = os.environ.get("LT_USERNAME", "")
    access_key = os.environ.get("LT_ACCESS_KEY", "")
    if not username or not access_key:
        return {
            "status": "skipped",
            "summary": "Skipped Kane run: LT credentials not available.",
            "one_liner": "",
            "steps": [],
            "final_state": {},
            "duration": None,
            "test_url": "",
        }

    playwright_version = ""
    try:
        result = subprocess.run(
            ["playwright", "--version"], capture_output=True, text=True, check=False
        )
        parts = result.stdout.strip().split()
        playwright_version = parts[1] if len(parts) >= 2 else ""
    except Exception:
        pass

    session_name = f"AC-{index:03d} | {description[:80].strip()}"

    caps = {
        "browserName": "Chrome",
        "browserVersion": "latest",
        "LT:Options": {
            "platform": "Windows 10",
            "build": build_name(),
            "name": session_name,
            "user": username,
            "accessKey": access_key,
            "network": True,
            "video": True,
            "console": True,
            "tunnel": False,
            "tunnelName": "",
            "playwrightClientVersion": playwright_version,
        },
    }
    ws_endpoint = (
        "wss://cdp.lambdatest.com/playwright?capabilities="
        + urllib.parse.quote(json.dumps(caps))
    )
    task = _get_kane_task(description)
    command = [
        KANE_EXE, "run", task,
        "--username", username,
        "--access-key", access_key,
        "--ws-endpoint", ws_endpoint,
        "--agent",
        "--headless",
        "--timeout", "120",
        "--max-steps", "30",
        "--code-export",
        "--code-language", "python",
        "--skip-code-validation",
    ]
    run_start = time.time()
    completed = subprocess.run(command, capture_output=True, text=True, check=False,
                               encoding="utf-8", errors="replace")
    duration = round(time.time() - run_start, 1)
    exit_status = EXIT_STATUS.get(completed.returncode, "error")

    combined = completed.stdout + "\n" + completed.stderr
    parsed = _parse_kane_output(combined)
    run_end = parsed["run_end"]

    if not run_end:
        raw_output = combined.strip()
        diagnostic = raw_output[:500] if raw_output else "Kane CLI produced no output."
        return {
            "status": exit_status,
            "summary": diagnostic,
            "one_liner": "",
            "steps": [],
            "final_state": {},
            "duration": duration,
            "test_url": parsed["test_url"],
            "session_id": parsed["session_id"],
            "code_export_dir": parsed["code_export_dir"],
            "share_link": parsed["share_link"],
            "testcase_link": parsed["testcase_link"],
        }

    return {
        "status": run_end.get("status", exit_status),
        "summary": run_end.get("summary", ""),
        "one_liner": run_end.get("one_liner", ""),
        "steps": parsed["step_summaries"],
        "final_state": run_end.get("final_state", {}),
        "duration": duration,
        "test_url": parsed["test_url"],
        "session_id": parsed["session_id"],
        "code_export_dir": parsed["code_export_dir"],
        "share_link": parsed["share_link"],
        "testcase_link": parsed["testcase_link"],
    }


def load_demo_results(criteria):
    """Load pre-generated demo Kane results, mapped to the actual criteria list."""
    demo_path = Path("ci/demo_kane_results.json")
    if not demo_path.exists():
        raise FileNotFoundError(
            f"DEMO_MODE requires ci/demo_kane_results.json — file not found at {demo_path}"
        )
    demo_data = json.loads(demo_path.read_text(encoding="utf-8"))
    results = []
    for i, criterion in enumerate(criteria):
        if i < len(demo_data):
            results.append(demo_data[i])
        else:
            results.append({
                "status": "passed",
                "summary": f"Demo result for: {criterion[:60]}",
                "one_liner": f"Criterion verified (demo) — {criterion[:50]}",
                "steps": ["Demo step 1", "Demo step 2"],
                "final_state": {},
                "duration": 42,
                "test_url": "https://automation.lambdatest.com/test?testID=demo",
            })
    return results


def emit_metrics(stage, duration_seconds, cache_hit=False, criteria_count=0):
    """Append timing to pipeline_metrics.json — no-op if file absent."""
    metrics_path = Path("reports/pipeline_metrics.json")
    try:
        metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
        metrics.setdefault("stages", {})[stage] = {
            "duration_seconds": round(duration_seconds, 2),
            "cache_hit": cache_hit,
            "criteria_count": criteria_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_path.write_text(json.dumps(metrics, indent=2))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# TestMD mode — run kane-cli testmd run per .md file (no --ws-endpoint)
# Activated when kaneai.use_testmd: true in agentic-stlc.config.yaml AND
# kane/testmd/*.md files exist.
# ---------------------------------------------------------------------------

def _discover_testmd_files(testmd_dir: str = "kane/testmd") -> list[Path]:
    """Return sorted list of *_test.md files in testmd_dir."""
    d = Path(testmd_dir)
    if not d.exists():
        return []
    return sorted(d.glob("*_test.md"))


def run_kane_testmd(index: int, description: str, testmd_file: Path) -> dict:
    """Run a single kane-cli testmd run and return a Kane result dict.

    Uses the same shared _parse_kane_output() as run_kane so TMS links
    (ShareLink, TestCase), code exports, and session IDs are all captured.
    """
    username = os.environ.get("LT_USERNAME", "")
    access_key = os.environ.get("LT_ACCESS_KEY", "")
    if not username or not access_key:
        return {
            "status": "skipped",
            "summary": "Skipped Kane TestMD run: LT credentials not available.",
            "one_liner": "", "steps": [], "final_state": {},
            "duration": None, "test_url": "", "session_id": "",
            "code_export_dir": "", "share_link": "", "testcase_link": "",
        }

    command = [
        KANE_EXE, "testmd", "run", str(testmd_file),
        "--agent", "--headless",
        "--timeout", "120",
        "--max-steps", "30",
        "--on-lock-conflict", "wait",
        "--retry",
    ]
    print(f"  [testmd] AC-{index:03d}: {testmd_file.name}")
    run_start = time.time()
    completed = subprocess.run(command, capture_output=True, text=True, check=False,
                               encoding="utf-8", errors="replace")
    duration = round(time.time() - run_start, 1)
    exit_status = EXIT_STATUS.get(completed.returncode, "error")

    combined = completed.stdout + "\n" + completed.stderr
    parsed = _parse_kane_output(combined)
    run_end = parsed["run_end"]

    if run_end:
        status = "passed" if run_end.get("passed") else "failed"
        summary = run_end.get("summary", run_end.get("one_liner", ""))
        one_liner = run_end.get("one_liner", summary)
        final_state = run_end.get("final_state", {})
    else:
        status = exit_status
        summary = f"TestMD run {exit_status} (exit={completed.returncode}): {combined[:300]}"
        one_liner = ""
        final_state = {}

    return {
        "status": status,
        "summary": summary,
        "one_liner": one_liner,
        "steps": parsed["step_summaries"],
        "final_state": final_state,
        "duration": duration,
        "test_url": parsed["test_url"],
        "session_id": parsed["session_id"],
        "code_export_dir": parsed["code_export_dir"],
        "share_link": parsed["share_link"],
        "testcase_link": parsed["testcase_link"],
    }


def _run_kane_testmd_indexed(args) -> dict:
    index, description, testmd_file = args
    return run_kane_testmd(index, description, testmd_file)


def _load_pipeline_config() -> dict:
    """Load agentic-stlc.config.yaml from current working directory."""
    config_path = Path(os.environ.get("AGENTIC_STLC_CONFIG", "agentic-stlc.config.yaml"))
    if not config_path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def main():
    args = parse_args()
    demo_mode = args.demo_mode or os.environ.get("DEMO_MODE", "false").lower() == "true"

    Path("reports").mkdir(exist_ok=True)
    print_stage_header("1", "ANALYZE_REQUIREMENTS", "Parse requirements and run KaneAI functional verification")

    req_path = Path(args.requirements)
    criteria = []
    if req_path.is_dir():
        for req_file in sorted(req_path.glob("*.txt")):
            criteria.extend(extract_acceptance_criteria(req_file.read_text(encoding="utf-8")))
    else:
        criteria = extract_acceptance_criteria(req_path.read_text(encoding="utf-8"))

    today = datetime.now(timezone.utc).date().isoformat()
    stage_start = time.time()

    # Detect TestMD mode: config flag + testmd files present
    pipeline_config = _load_pipeline_config()
    use_testmd = pipeline_config.get("kaneai", {}).get("use_testmd", False)
    testmd_dir = pipeline_config.get("kaneai", {}).get("testmd_output_dir", "kane/testmd")
    testmd_files = _discover_testmd_files(testmd_dir) if use_testmd else []
    if use_testmd and testmd_files:
        print(f"[Stage 1] TestMD mode: {len(testmd_files)} .md files found in {testmd_dir}/")
    elif use_testmd:
        print(f"[Stage 1] TestMD mode requested but no .md files found in {testmd_dir}/ — using direct run mode")

    if demo_mode:
        print(f"[DEMO_MODE] Loading pre-generated Kane results for {len(criteria)} criteria")
        results = load_demo_results(criteria)
        cache_hit = True
    elif args.skip_kane:
        results = [{
            "status": "pending", "summary": "Kane run not attempted.",
            "one_liner": "", "steps": [], "final_state": {}, "duration": None, "test_url": "",
            "session_id": "", "code_export_dir": "",
        } for _ in criteria]
        cache_hit = False
    elif use_testmd and testmd_files:
        # TestMD mode: pair each criterion with its .md file (by index or filename match)
        _configure_kane_project()
        testmd_args = []
        for i, description in enumerate(criteria, start=1):
            # Match by AC index (ac_001_* -> criterion 1), fall back to positional
            ac_slug = f"ac_{i:03d}_"
            matched = next((f for f in testmd_files if f.name.startswith(ac_slug)), None)
            if matched is None and i <= len(testmd_files):
                matched = testmd_files[i - 1]  # positional fallback
            if matched:
                testmd_args.append((i, description, matched))
            else:
                print(f"  [warn] No TestMD file for AC-{i:03d} — will be skipped")
        workers = min(int(os.getenv("KANE_PARALLEL_WORKERS", 10)), len(testmd_args)) if testmd_args else 1
        print(f"[Stage 1] Running Kane TestMD in parallel (workers={workers}, {len(testmd_args)} files)...")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            paired_results = list(executor.map(_run_kane_testmd_indexed, testmd_args))
        # Rebuild results in original criterion order (unmatched criteria = skipped)
        result_map = {args[0]: r for args, r in zip(testmd_args, paired_results)}
        results = []
        for i in range(1, len(criteria) + 1):
            results.append(result_map.get(i, {
                "status": "skipped", "summary": "No TestMD file matched.",
                "one_liner": "", "steps": [], "final_state": {}, "duration": None,
                "test_url": "", "session_id": "", "code_export_dir": "",
            }))
        cache_hit = False
    else:
        _configure_kane_project()
        workers = min(int(os.getenv("KANE_PARALLEL_WORKERS", 10)), len(criteria)) if criteria else 1
        print(f"[Stage 1] Running KaneAI in parallel (workers={workers}, {len(criteria)} criteria) — code export enabled...")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(_run_kane_indexed, enumerate(criteria, start=1)))
        cache_hit = False

    analyzed = []
    kane_results = []

    for index, (description, kane) in enumerate(zip(criteria, results), start=1):
        test_url = kane.get("test_url", "")
        item = {
            "id": f"AC-{index:03d}",
            "title": make_title(description),
            "description": description,
            "url": TARGET_URL,
            "kane_status": kane["status"],
            "kane_one_liner": kane.get("one_liner", ""),
            "kane_summary": kane["summary"],
            "kane_steps": kane.get("steps", []),
            "kane_final_state": kane["final_state"],
            "kane_duration": kane["duration"],
            "kane_links": [u for u in [
                kane.get("share_link", ""),
                kane.get("testcase_link", ""),
                test_url,
            ] if u],
            "kane_share_link": kane.get("share_link", ""),
            "kane_testcase_link": kane.get("testcase_link", ""),
            "kane_session_id": kane.get("session_id", ""),
            "kane_code_export_dir": kane.get("code_export_dir", ""),
            "last_analyzed": today,
        }
        analyzed.append(item)
        kane_results.append({
            "requirement_id": item["id"],
            "title": item["title"],
            "status": item["kane_status"],
            "one_liner": item["kane_one_liner"],
            "summary": item["kane_summary"],
            "steps": item["kane_steps"],
            "final_state": item["kane_final_state"],
            "duration": item["kane_duration"],
            "link": test_url,
            "url": item["url"],
        })

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(analyzed, indent=2) + "\n", encoding="utf-8")

    kane_path = Path(args.kane_results)
    kane_path.parent.mkdir(parents=True, exist_ok=True)
    kane_path.write_text(json.dumps(kane_results, indent=2) + "\n", encoding="utf-8")

    print(f"{'ID':8} {'Kane':<9} {'Title':<40} {'Link'}")
    for item in analyzed:
        link = item.get("kane_links", [""])[0] if item.get("kane_links") else ""
        print(f"{item['id']:8} {item['kane_status']:<9} {item['title']:40.40} {link}")

    elapsed = time.time() - stage_start
    mode_label = "demo" if demo_mode else ("cached" if cache_hit else "live")
    passed_count = sum(1 for a in analyzed if a["kane_status"] == "passed")
    failed_count = sum(1 for a in analyzed if a["kane_status"] == "failed")

    print_stage_result("1", "ANALYZE_REQUIREMENTS", {
        "Requirements parsed":  len(analyzed),
        "Criteria analyzed":    f"{len(analyzed)} ({mode_label}, workers={len(criteria) if criteria else 1})",
        "Kane passed":          f"{passed_count}/{len(analyzed)}",
        "Kane failed":          failed_count,
        "Pass rate":            f"{round(passed_count / len(analyzed) * 100, 1) if analyzed else 0}%",
        "Duration":             f"{elapsed:.1f}s",
        "Output":               args.output,
    })
    emit_metrics("stage1_kane", elapsed, cache_hit=cache_hit, criteria_count=len(criteria))


if __name__ == "__main__":
    main()
