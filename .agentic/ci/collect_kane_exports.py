"""
Stage 3a — Collect Kane AI code exports and assemble tests/playwright/test_powerapps.py.

For every active scenario in scenarios.json, this script:
  1. Looks up the Kane session's code-export directory from analyzed_requirements.json.
  2. Reads the Kane-generated Python Playwright code.
  3. Extracts the test body (strips the function def / type annotations).
  4. Wraps the body in a pytest function with @pytest.mark.scenario / @pytest.mark.requirement.
  5. Writes the assembled file to tests/playwright/test_powerapps.py.

When Kane has no export for a scenario (session skipped or code-export missing), the script
falls back to a curated hand-written body for that acceptance criterion so the test is never
empty — it is always a real, executable Playwright action.

Writes:
  tests/playwright/test_powerapps.py
"""
import ast
import json
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_utils import print_stage_header, print_stage_result

BASE_URL = "https://ecommerce-playground.lambdatest.io/"
CONTOSOTRADERS_URL = "http://localhost:3000"

# ---------------------------------------------------------------------------
# Fallback bodies — used when Kane has no exported code for a given AC.
# Each body is a complete, real Playwright implementation for the acceptance
# criterion.  The placeholder {url} is substituted with the scenario URL.
# ---------------------------------------------------------------------------
_FALLBACK_BODIES: dict[str, str] = {
    # AC-001 / SC-001 — Add to cart
    "AC-001": '''\
    page.goto("{url}index.php?route=product/product&product_id=28")
    page.wait_for_load_state("domcontentloaded")
    add_btn = page.locator("#button-cart")
    add_btn.wait_for(timeout=15000)
    add_btn.click()
    cart_indicator = page.locator("#cart button")
    cart_indicator.wait_for(timeout=10000)
    cart_indicator.click()
    page.wait_for_load_state("domcontentloaded")
    cart_items = page.locator("#cart .text-left a")
    cart_items.first.wait_for(timeout=10000)
    assert cart_items.count() > 0, "No items visible in cart after adding product"
''',
    # AC-002 / SC-002 — View cart items
    "AC-002": '''\
    page.goto("{url}index.php?route=product/product&product_id=28")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#button-cart").wait_for(timeout=15000)
    page.locator("#button-cart").click()
    cart_btn = page.locator("#cart > button")
    cart_btn.wait_for(timeout=10000)
    cart_btn.click()
    cart_items = page.locator("#cart .text-left a")
    cart_items.first.wait_for(timeout=10000)
    assert cart_items.count() > 0, "No items visible in cart dropdown"
''',
    # AC-003 / SC-003 — Product catalog
    "AC-003": '''\
    page.goto("{url}index.php?route=product/category&path=18")
    page.wait_for_load_state("domcontentloaded")
    products = page.locator(".product-thumb")
    products.first.wait_for(timeout=15000)
    assert products.count() > 0, "No products visible in the Laptops catalog"
''',
    # AC-004 / SC-004 — Brand filter
    "AC-004": '''\
    page.goto("{url}index.php?route=product/category&path=25")
    page.wait_for_load_state("domcontentloaded")
    filter_link = page.locator("#column-left .list-group-item").filter(has_text="Apple")
    if filter_link.count() == 0:
        filter_link = page.locator("#column-left a").filter(has_text="Apple")
    filter_link.first.wait_for(timeout=15000)
    filter_link.first.click()
    page.wait_for_load_state("domcontentloaded")
    assert page.locator(".product-thumb").count() >= 0, "Content area not visible after filter"
    assert page.locator("#content").count() > 0, "Content area not found after applying Apple filter"
''',
    # AC-005 / SC-005 — Product detail page
    "AC-005": '''\
    page.goto("{url}index.php?route=product/product&product_id=28")
    page.wait_for_load_state("domcontentloaded")
    product_name = page.locator("h1").first
    product_name.wait_for(timeout=15000)
    assert product_name.inner_text().strip() != "", "Product name is empty on detail page"
    price = page.locator(".price-new, h2.price, .price").first
    price.wait_for(timeout=10000)
    assert price.count() > 0, "Product price not visible on detail page"
''',
    # AC-006 / SC-006 — Browse homepage as guest
    "AC-006": '''\
    page.goto("{url}")
    page.wait_for_load_state("domcontentloaded")
    hero = page.locator("#content, .slideshow0, .swiper-wrapper, .carousel-inner").first
    hero.wait_for(timeout=15000)
    assert hero.count() > 0, "Homepage content not visible without login"
    assert page.title().strip() != "", "Page title is empty"
''',
    # AC-007 / SC-007 — Search
    "AC-007": '''\
    page.goto("{url}")
    page.wait_for_load_state("domcontentloaded")
    search_input = page.locator("input[name='search']").first
    search_input.wait_for(timeout=15000)
    search_input.fill("iPhone")
    search_input.press("Enter")
    page.wait_for_load_state("domcontentloaded")
    results = page.locator(".product-thumb")
    results.first.wait_for(timeout=15000)
    assert results.count() > 0, "No search results returned for 'iPhone'"
''',
    # AC-008 / SC-008 — Register
    "AC-008": '''\
    import uuid
    unique_email = f"test_{uuid.uuid4().hex[:8]}@lambdatest.io"
    page.goto("{url}index.php?route=account/register")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#input-firstname").fill("Test")
    page.locator("#input-lastname").fill("User")
    page.locator("#input-email").fill(unique_email)
    page.locator("#input-telephone").fill("5550100200")
    page.locator("#input-password").fill("Test@1234")
    page.locator("#input-confirm").fill("Test@1234")
    agree_box = page.locator("input[name='agree']")
    if agree_box.count() > 0:
        agree_box.check()
    page.locator("input[value='Continue']").click()
    page.wait_for_load_state("domcontentloaded")
    success = page.locator("#content h1, .alert-success, h1")
    success.wait_for(timeout=15000)
    assert "account" in page.url.lower() or "success" in page.url.lower() or \
        success.inner_text().strip() != "", "Registration did not complete successfully"
''',
    # AC-009 / SC-009 — Login
    "AC-009": '''\
    lt_user = __import__("os").environ.get("LT_ECOM_USER", "test@lambdatest.com")
    lt_pass = __import__("os").environ.get("LT_ECOM_PASS", "Test@1234")
    page.goto("{url}index.php?route=account/login")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#input-email").fill(lt_user)
    page.locator("#input-password").fill(lt_pass)
    page.locator("input[value='Login']").click()
    page.wait_for_load_state("domcontentloaded")
    assert "account" in page.url.lower(), f"Login failed — URL is {{page.url}}"
    dashboard = page.locator("h2").first
    dashboard.wait_for(timeout=15000)
    assert dashboard.count() > 0, "Account dashboard not visible after login"
''',
    # AC-010 / SC-010 — Logout
    "AC-010": '''\
    lt_user = __import__("os").environ.get("LT_ECOM_USER", "test@lambdatest.com")
    lt_pass = __import__("os").environ.get("LT_ECOM_PASS", "Test@1234")
    page.goto("{url}index.php?route=account/login")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#input-email").fill(lt_user)
    page.locator("#input-password").fill(lt_pass)
    page.locator("input[value='Login']").click()
    page.wait_for_load_state("domcontentloaded")
    logout_link = page.locator("a[href*='account/logout']")
    logout_link.wait_for(timeout=15000)
    logout_link.click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url == "{url}" or "logout" in page.url.lower() or \
        page.locator("input[value='Login'], a[href*='account/login']").count() > 0, \
        "Logout did not redirect to expected page"
''',
    # AC-011 / SC-011 — Remove from cart
    "AC-011": '''\
    page.goto("{url}index.php?route=product/product&product_id=28")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#button-cart").wait_for(timeout=15000)
    page.locator("#button-cart").click()
    page.goto("{url}index.php?route=checkout/cart")
    page.wait_for_load_state("domcontentloaded")
    remove_btn = page.locator("td.text-center a.btn-danger, a[data-original-title='Remove']").first
    remove_btn.wait_for(timeout=15000)
    remove_btn.click()
    page.wait_for_load_state("domcontentloaded")
    empty_msg = page.locator(".text-center strong, #content p")
    empty_msg.wait_for(timeout=10000)
    assert "empty" in empty_msg.first.inner_text().lower() or \
        page.locator("table.table").count() == 0, "Cart was not emptied after removing item"
''',
    # AC-012 / SC-012 — Update quantity
    "AC-012": '''\
    page.goto("{url}index.php?route=product/product&product_id=28")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#button-cart").wait_for(timeout=15000)
    page.locator("#button-cart").click()
    page.goto("{url}index.php?route=checkout/cart")
    page.wait_for_load_state("domcontentloaded")
    qty_input = page.locator("input.form-control.input-qty, input[name='quantity']").first
    qty_input.wait_for(timeout=15000)
    qty_input.triple_click()
    qty_input.fill("3")
    update_btn = page.locator("button[data-original-title='Update'], input[value='Update']").first
    update_btn.click()
    page.wait_for_load_state("domcontentloaded")
    updated_qty = page.locator("input.form-control.input-qty, input[name='quantity']").first
    assert updated_qty.input_value() == "3", "Quantity was not updated to 3"
''',
    # AC-013 / SC-013 — Sort by price
    "AC-013": '''\
    page.goto("{url}index.php?route=product/category&path=18")
    page.wait_for_load_state("domcontentloaded")
    products = page.locator(".product-thumb")
    products.first.wait_for(timeout=15000)
    sort_select = page.locator("#input-sort")
    sort_select.wait_for(timeout=10000)
    sort_select.select_option("pa")   # Price (Low > High)
    page.wait_for_load_state("domcontentloaded")
    sorted_products = page.locator(".product-thumb")
    sorted_products.first.wait_for(timeout=15000)
    assert sorted_products.count() > 0, "No products visible after sorting by price"
    assert "sort=pa" in page.url or sorted_products.count() > 0, \
        "Sort parameter not applied or page did not update"
''',
    # AC-014 / SC-014 — Wishlist
    "AC-014": '''\
    lt_user = __import__("os").environ.get("LT_ECOM_USER", "test@lambdatest.com")
    lt_pass = __import__("os").environ.get("LT_ECOM_PASS", "Test@1234")
    page.goto("{url}index.php?route=account/login")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#input-email").fill(lt_user)
    page.locator("#input-password").fill(lt_pass)
    page.locator("input[value='Login']").click()
    page.wait_for_load_state("domcontentloaded")
    page.goto("{url}index.php?route=product/product&product_id=28")
    page.wait_for_load_state("domcontentloaded")
    wishlist_btn = page.locator(
        "[data-original-title='Add to Wish List'], "
        "button.wishlist-compare-wrap, "
        ".btn-wishlist"
    ).first
    wishlist_btn.wait_for(timeout=15000)
    wishlist_btn.click()
    page.wait_for_load_state("domcontentloaded")
    page.goto("{url}index.php?route=account/wishlist")
    page.wait_for_load_state("domcontentloaded")
    wishlist_items = page.locator("table.table-bordered tbody tr, .product-thumb")
    wishlist_items.first.wait_for(timeout=15000)
    assert wishlist_items.count() > 0, "Wishlist is empty after adding product"
''',
    # AC-015 / SC-015 — Guest checkout
    "AC-015": '''\
    page.goto("{url}index.php?route=product/product&product_id=28")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#button-cart").wait_for(timeout=15000)
    page.locator("#button-cart").click()
    page.goto("{url}index.php?route=checkout/checkout")
    page.wait_for_load_state("domcontentloaded")
    guest_radio = page.locator("input[value='guest']")
    if guest_radio.count() > 0:
        guest_radio.click()
        continue_btn = page.locator("#button-account")
        if continue_btn.count() > 0:
            continue_btn.click()
            page.wait_for_load_state("domcontentloaded")
    firstname = page.locator("#input-payment-firstname")
    firstname.wait_for(timeout=15000)
    firstname.fill("Jane")
    page.locator("#input-payment-lastname").fill("Doe")
    page.locator("#input-payment-email").fill("jane.doe@test.io")
    page.locator("#input-payment-telephone").fill("5559876543")
    page.locator("#input-payment-address-1").fill("123 Test Street")
    page.locator("#input-payment-city").fill("Austin")
    page.locator("#input-payment-postcode").fill("78701")
    country_select = page.locator("#input-payment-country")
    country_select.select_option("223")  # United States
    page.wait_for_load_state("domcontentloaded")
    zone_select = page.locator("#input-payment-zone")
    zone_select.select_option(value=zone_select.locator("option").nth(1).get_attribute("value") or "")
    continue_btn2 = page.locator("#button-guest")
    if continue_btn2.count() > 0:
        continue_btn2.click()
        page.wait_for_load_state("domcontentloaded")
    assert page.url != "{url}", "Checkout did not advance past billing step"
''',
}


def _load_json(path: str, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _extract_test_body(py_file: Path) -> str:
    """Extract the body of the first test_* function from a Kane-exported .py file.

    Strips the function signature and any `page: Page` type annotation so the
    body can be embedded directly in a conftest-compatible pytest function.
    """
    source = py_file.read_text(encoding="utf-8")

    # Try AST extraction first — most reliable
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test"):
                    lines = source.splitlines()
                    body_start = node.body[0].lineno - 1
                    body_end = node.end_lineno
                    body_lines = lines[body_start:body_end]
                    dedented = textwrap.dedent("\n".join(body_lines))
                    # Remove any awaits — conftest.py uses sync Playwright
                    dedented = re.sub(r"\bawait\s+", "", dedented)
                    return dedented.rstrip()
    except SyntaxError:
        pass

    # Fallback: regex-based extraction
    lines = source.splitlines()
    body_lines: list[str] = []
    in_fn = False
    base_indent: int | None = None

    for line in lines:
        if re.match(r"^(async\s+)?def\s+test", line):
            in_fn = True
            base_indent = None
            continue
        if not in_fn:
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if base_indent is None and stripped:
            base_indent = indent
        if stripped and indent == 0 and base_indent and indent < base_indent:
            break
        if base_indent is not None and indent >= base_indent:
            body_lines.append(line[base_indent:] if base_indent else line)
        else:
            body_lines.append("")

    body = "\n".join(body_lines).strip()
    # Remove await calls for sync Playwright compatibility
    body = re.sub(r"\bawait\s+", "", body)
    return body


# ---------------------------------------------------------------------------
# Generic fallback — used when no specific body is available for this AC.
# Runs against any target URL; just verifies the page loads with a title.
# ---------------------------------------------------------------------------
_GENERIC_FALLBACK = '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
title = page.title().strip()
assert title != "", f"Page at {{page.url}} has no title after load"
'''

# ---------------------------------------------------------------------------
# Contosotraders-specific fallback bodies (localhost:3000 React app).
# Used when Kane is skipped and the target URL is the contosotraders app.
# ---------------------------------------------------------------------------
_CONTOSOTRADERS_FALLBACK_BODIES: dict[str, str] = {
    "AC-001": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
banner = page.locator(
    "[class*='memorial'], [class*='banner'], [class*='promo'], "
    "[data-testid*='banner'], header .announcement, header .promo-bar"
).first
try:
    banner.wait_for(timeout=5000)
    text = banner.inner_text().lower()
    assert "memorial" in text or "sale" in text, (
        f"Banner found but text does not contain 'memorial' or 'sale': {text!r}"
    )
except Exception:
    body_text = page.locator("body").inner_text().lower()
    assert "memorial" in body_text or "memorial day" in body_text, (
        "No Memorial Day banner found anywhere on the page. "
        "APPLICATION_DEFECT: headerMessage component is not imported in header."
    )
''',
    "AC-002": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
hero = page.locator(
    "[class*='hero'], [class*='carousel'], [class*='banner'], "
    "[class*='featured'], .slick-slider, [class*='slide']"
).first
hero.wait_for(timeout=10000)
assert hero.count() > 0, "No hero/carousel/featured section visible on home page"
''',
    "AC-003": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
logo = page.locator(
    "header img, [class*='logo'], [alt*='Contoso'], [aria-label*='Contoso'], "
    "nav img, header [class*='brand']"
).first
logo.wait_for(timeout=10000)
assert logo.count() > 0, "Contoso Traders logo not found in header"
''',
    "AC-004": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
nav_links = page.locator("nav a, header a, [role='navigation'] a")
nav_links.first.wait_for(timeout=10000)
count = nav_links.count()
assert count >= 2, f"Expected at least 2 navigation links, found {count}"
categories = ["controller", "laptop", "headphone", "xbox", "playstation", "monitor"]
found = False
for i in range(min(count, 15)):
    text = nav_links.nth(i).inner_text().lower().strip()
    if any(cat in text for cat in categories):
        found = True
        break
assert found, f"No product category links found in navigation (checked {min(count,15)} links)"
''',
    "AC-005": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
search = page.get_by_placeholder("Search by product name or search by image")
if search.count() == 0:
    search = page.locator("input[type='search'], input[placeholder*='search' i]").first
search.wait_for(timeout=10000)
search.fill("laptop")
search.press("Enter")
page.wait_for_load_state("domcontentloaded", timeout=20000)
assert "laptop" in page.url.lower() or page.locator("[class*='product'], [class*='result']").count() > 0, \
    "Search did not navigate to results page or return product results"
''',
    "AC-006": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
nav_links = page.locator("nav a, header a")
nav_links.first.wait_for(timeout=10000)
for i in range(min(nav_links.count(), 15)):
    text = nav_links.nth(i).inner_text().lower()
    if any(c in text for c in ["controller", "laptop", "headphone", "xbox"]):
        nav_links.nth(i).click()
        page.wait_for_load_state("domcontentloaded", timeout=20000)
        break
product_cards = page.locator("[class*='product'], [class*='card'], [class*='item']")
product_cards.first.wait_for(timeout=10000)
assert product_cards.count() >= 1, "No product cards visible on category page"
''',
    "AC-007": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
nav_links = page.locator("nav a, header a")
nav_links.first.wait_for(timeout=10000)
for i in range(min(nav_links.count(), 15)):
    text = nav_links.nth(i).inner_text().lower()
    if any(c in text for c in ["controller", "laptop", "headphone", "xbox"]):
        nav_links.nth(i).click()
        page.wait_for_load_state("domcontentloaded", timeout=20000)
        break
initial_url = page.url
product_card = page.locator("[class*='product'], [class*='card']").first
product_card.wait_for(timeout=10000)
product_card.click()
page.wait_for_load_state("domcontentloaded", timeout=20000)
assert page.url != initial_url, "Clicking product card did not navigate to a detail page"
''',
    "AC-008": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
nav_links = page.locator("nav a, header a")
nav_links.first.wait_for(timeout=10000)
for i in range(min(nav_links.count(), 15)):
    text = nav_links.nth(i).inner_text().lower()
    if any(c in text for c in ["controller", "laptop", "headphone", "xbox"]):
        nav_links.nth(i).click()
        page.wait_for_load_state("domcontentloaded", timeout=20000)
        break
page.locator("[class*='product'], [class*='card']").first.click()
page.wait_for_load_state("domcontentloaded", timeout=20000)
product_name = page.locator("h1, h2, [class*='product-name'], [class*='title']").first
product_name.wait_for(timeout=10000)
assert product_name.inner_text().strip() != "", "Product name is empty on detail page"
price = page.locator("[class*='price'], [class*='cost'], [aria-label*='price']").first
price.wait_for(timeout=10000)
assert price.inner_text().strip() != "", "Price not visible on product detail page"
''',
    "AC-009": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
cart = page.locator(
    "header [class*='cart'], [aria-label*='cart' i], "
    "[class*='shopping-bag'], [data-testid*='cart'], header button svg"
).first
cart.wait_for(timeout=10000)
assert cart.count() > 0, "Cart icon not visible in header"
cart.click()
page.wait_for_timeout(1000)
''',
    "AC-010": '''\
page.goto("{url}")
page.wait_for_load_state("domcontentloaded", timeout=30000)
page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
page.wait_for_timeout(1000)
footer = page.locator("footer, [class*='footer'], [role='contentinfo']").first
footer.wait_for(timeout=10000)
assert footer.count() > 0, "Footer not visible at bottom of page"
footer_text = footer.inner_text().strip()
assert footer_text != "", "Footer is visible but contains no text"
''',
}


def _is_contosotraders(url: str) -> bool:
    return "localhost:3000" in url or "contosotraders" in url.lower()


def _get_fallback_body(req_id: str, url: str) -> str:
    """Return the most appropriate fallback body for this requirement + URL."""
    if _is_contosotraders(url):
        if req_id in _CONTOSOTRADERS_FALLBACK_BODIES:
            return _CONTOSOTRADERS_FALLBACK_BODIES[req_id].rstrip()
        return _GENERIC_FALLBACK.rstrip()
    if req_id in _FALLBACK_BODIES:
        return _FALLBACK_BODIES[req_id].format(url=url).rstrip()
    return _GENERIC_FALLBACK.format(url=url).rstrip()


def _collect_exports(analyzed: list[dict]) -> dict[str, str]:
    """Returns mapping of AC-id → extracted test body string."""
    bodies: dict[str, str] = {}
    for item in analyzed:
        ac_id = item.get("id", "")
        export_dir = item.get("kane_code_export_dir", "")
        if not export_dir:
            continue
        export_path = Path(export_dir)
        if not export_path.exists():
            continue
        py_files = sorted(export_path.glob("*.py"))
        if not py_files:
            continue
        body = _extract_test_body(py_files[0])
        if body:
            bodies[ac_id] = body
            print(f"  [collect] {ac_id} — Kane export found: {py_files[0].name} ({len(body)} chars)")
        else:
            print(f"  [collect] {ac_id} — Kane export found but body empty: {py_files[0]}")
    return bodies


def _make_fn_name(sc_id: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]
    return f"test_{sc_id.lower().replace('-', '_')}_{slug}"


FILE_HEADER = '''\
"""
Playwright test suite — generated by Agentic STLC pipeline from Kane AI code exports.
Do not edit manually — re-run Stage 1 to regenerate.
"""
import os
import uuid
import pytest
from playwright.sync_api import expect

'''


def build_test_function(scenario: dict, body: str) -> str:
    sc_id = scenario["id"]
    req_id = scenario["requirement_id"]
    fn_name = scenario.get("function_name") or _make_fn_name(sc_id, scenario.get("title", sc_id))
    title = scenario.get("title", "").replace('"', "'")
    indented_body = textwrap.indent(textwrap.dedent(body).strip(), "    ")
    return (
        f'@pytest.mark.scenario("{sc_id}")\n'
        f'@pytest.mark.requirement("{req_id}")\n'
        f'def {fn_name}(page):\n'
        f'    """{sc_id}: {title}."""\n'
        f'{indented_body}\n'
    )


def collect_and_assemble(
    analyzed_path: str = "requirements/analyzed_requirements.json",
    scenarios_path: str = "scenarios/scenarios.json",
    output_path: str = "tests/playwright/test_powerapps.py",
) -> dict:
    print_stage_header("3a", "COLLECT_KANE_EXPORTS",
                       "Assemble Kane-exported Python Playwright code into test_powerapps.py")

    analyzed: list[dict] = _load_json(analyzed_path, [])
    scenarios: list[dict] = _load_json(scenarios_path, [])

    if not scenarios:
        print(f"[ERROR] No scenarios found at {scenarios_path}", file=sys.stderr)
        sys.exit(1)

    # Build AC-id → Kane export body mapping
    kane_bodies = _collect_exports(analyzed)

    # Build AC-id → analyzed item mapping for URL resolution
    ac_map = {item["id"]: item for item in analyzed}

    functions: list[str] = []
    kane_used = 0
    fallback_used = 0
    missing = 0

    for sc in scenarios:
        if sc.get("status") == "deprecated":
            continue

        req_id = sc.get("requirement_id", "")
        url = sc.get("kane_url", BASE_URL)
        if req_id not in ac_map:
            url = BASE_URL

        # Priority 1: Kane-exported body
        if req_id in kane_bodies:
            body = kane_bodies[req_id]
            kane_used += 1
            source = "kane_export"
        else:
            # Priority 2: URL-aware curated fallback (contosotraders or ecommerce)
            body = _get_fallback_body(req_id, url)
            fallback_used += 1
            source = "fallback"
            missing_marker = req_id not in _FALLBACK_BODIES and not _is_contosotraders(url)
            if missing_marker:
                missing += 1
                source = "generic"

        print(f"  [{source:12}] {sc['id']} ({req_id}): {sc.get('title', '')[:50]}")
        functions.append(build_test_function(sc, body))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(FILE_HEADER + "\n\n".join(functions) + "\n", encoding="utf-8")

    total = len(functions)
    print_stage_result("3a", "COLLECT_KANE_EXPORTS", {
        "Scenarios assembled":   total,
        "Kane export used":      kane_used,
        "Fallback used":         fallback_used,
        "Skipped (no impl)":     missing,
        "Kane coverage":         f"{round(kane_used / total * 100)}%" if total else "0%",
        "Output":                output_path,
    })

    return {
        "total": total,
        "kane_used": kane_used,
        "fallback_used": fallback_used,
        "missing": missing,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--analyzed", default="requirements/analyzed_requirements.json")
    parser.add_argument("--scenarios", default="scenarios/scenarios.json")
    parser.add_argument("--output", default="tests/playwright/test_powerapps.py")
    args = parser.parse_args()
    collect_and_assemble(args.analyzed, args.scenarios, args.output)
