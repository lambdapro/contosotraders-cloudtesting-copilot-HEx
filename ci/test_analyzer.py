"""
Analyzes existing Playwright TypeScript/JavaScript test files to extract
coding patterns so generated tests match the existing codebase conventions.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
from dataclasses import dataclass, field


@dataclass
class TestProfile:
    import_style: str           # "lambda_setup" | "playwright_native"
    lambda_setup_path: str      # "../lambda.setup" or similar
    uses_describe: bool         # True if tests are wrapped in test.describe()
    navigation_pattern: str     # "beforeEach_goto" | "inline_goto" | "none"
    base_url: str               # e.g. "http://localhost:3000/"
    locator_strategies: list[str] = field(default_factory=list)  # ["getByText", "locator", ...]
    assertion_patterns: list[str] = field(default_factory=list)  # ["toBeTruthy", "toBeVisible", ...]
    most_relevant_file: str = ""   # test file most similar to the new requirement
    existing_imports: str = ""     # raw import block from most_relevant_file
    test_file_language: str = "typescript"  # "typescript" | "javascript"


def _gh_file(owner: str, name: str, path: str) -> str | None:
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{name}/contents/{path}", "--jq", ".content"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip().strip('"')
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except Exception:
        return None


def _score_relevance(content: str, requirement: str) -> int:
    """Score how relevant a test file is to the given requirement (higher = more relevant)."""
    req_words = set(re.findall(r"\w+", requirement.lower()))
    file_words = set(re.findall(r"\w+", content.lower()))
    return len(req_words & file_words)


def extract_patterns(
    owner: str,
    name: str,
    test_files: list[str],
    requirement: str = "",
) -> TestProfile:
    """
    Read test files from GitHub and extract coding patterns.

    Args:
        owner: GitHub repo owner.
        name: GitHub repo name.
        test_files: List of spec file paths (from RepoProfile.existing_test_files).
        requirement: The new requirement string (used to find the most relevant file).

    Returns:
        TestProfile describing how existing tests are structured.
    """
    contents: dict[str, str] = {}
    for fpath in test_files:
        content = _gh_file(owner, name, fpath)
        if content:
            contents[fpath] = content

    if not contents:
        return TestProfile(
            import_style="playwright_native",
            lambda_setup_path="",
            uses_describe=False,
            navigation_pattern="beforeEach_goto",
            base_url="http://localhost:3000/",
        )

    # Find most relevant file to the requirement
    most_relevant = max(contents.keys(), key=lambda f: _score_relevance(contents[f], requirement)) \
        if requirement else next(iter(contents))

    # Analyze patterns across all files, weighting most_relevant file
    all_content = "\n".join(contents.values())
    primary = contents[most_relevant]

    # Import style
    lambda_setup_path = ""
    import_style = "playwright_native"
    m = re.search(r"import\s+\w+\s+from\s+['\"](\.\./lambda\.setup|\.\.\/lambda\.setup)['\"]",
                  all_content)
    if m:
        import_style = "lambda_setup"
        lambda_setup_path = m.group(1)
    else:
        m2 = re.search(r"import\s+\w+\s+from\s+['\"](\.\.[./]*lambda[./]setup[^'\"]*)['\"]",
                       all_content)
        if m2:
            import_style = "lambda_setup"
            lambda_setup_path = m2.group(1)

    # describe usage
    uses_describe = bool(re.search(r"test\.describe\s*\(", all_content))

    # Navigation pattern
    navigation_pattern = "none"
    base_url = "http://localhost:3000/"
    if re.search(r"beforeEach.*page\.goto", all_content, re.DOTALL):
        navigation_pattern = "beforeEach_goto"
        m_url = re.search(r"page\.goto\(['\"]([^'\"]+)['\"]", all_content)
        if m_url:
            base_url = m_url.group(1)
    elif re.search(r"page\.goto", all_content):
        navigation_pattern = "inline_goto"

    # Locator strategies used
    locator_strategies = []
    for strategy in ("getByText", "getByRole", "getByTitle", "getByLabel",
                     "getByPlaceholder", "locator", "getByTestId"):
        if strategy in all_content:
            locator_strategies.append(strategy)

    # Assertion patterns
    assertion_patterns = []
    for pattern in ("toBeTruthy", "toBeVisible", "toHaveTitle", "toHaveURL",
                    "toContainText", "toHaveText", "toBeChecked", "toHaveValue"):
        if pattern in all_content:
            assertion_patterns.append(pattern)

    # Language
    lang = "typescript" if any(f.endswith(".ts") for f in contents) else "javascript"

    # Extract import block from most relevant file
    import_lines = [line for line in primary.splitlines() if line.startswith("import ")]
    existing_imports = "\n".join(import_lines)

    print(f"[test_analyzer] import_style={import_style} uses_describe={uses_describe} "
          f"navigation={navigation_pattern} locators={locator_strategies}")
    print(f"[test_analyzer] most_relevant_file={most_relevant}")

    return TestProfile(
        import_style=import_style,
        lambda_setup_path=lambda_setup_path,
        uses_describe=uses_describe,
        navigation_pattern=navigation_pattern,
        base_url=base_url,
        locator_strategies=locator_strategies,
        assertion_patterns=assertion_patterns,
        most_relevant_file=most_relevant,
        existing_imports=existing_imports,
        test_file_language=lang,
    )
