import os
import time

from playwright.sync_api import sync_playwright

UE_EMAIL = os.environ.get("UE_EMAIL", "")
UE_PASSWORD = os.environ.get("UE_PASSWORD", "")


def build_ue_cart(items: list[dict], delivery_address: str, store_hint: str):
    """Log into Uber Eats, search for a store, add all items to cart."""
    if not UE_EMAIL or not UE_PASSWORD:
        raise RuntimeError("UE_EMAIL and UE_PASSWORD env vars are required")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        try:
            _login(page)
            store_url = _find_store(page, delivery_address, store_hint)
            _add_items_to_cart(page, store_url, items)
        finally:
            browser.close()


def _login(page):
    page.goto("https://www.ubereats.com/", wait_until="domcontentloaded")
    time.sleep(2)

    # Click Sign In
    sign_in = page.locator("a[href*='login'], button:has-text('Sign in')").first
    sign_in.click()
    time.sleep(1)

    # Enter email
    page.locator("input[name='email'], input[type='email']").first.fill(UE_EMAIL)
    page.keyboard.press("Enter")
    time.sleep(1)

    # Enter password
    page.locator("input[name='password'], input[type='password']").first.fill(UE_PASSWORD)
    page.keyboard.press("Enter")
    time.sleep(3)


def _find_store(page, delivery_address: str, store_hint: str) -> str:
    """Navigate to the UE homepage, set delivery address, search for store."""
    page.goto("https://www.ubereats.com/", wait_until="domcontentloaded")
    time.sleep(2)

    # Set delivery address if provided
    if delivery_address:
        addr_input = page.locator(
            "input[placeholder*='address'], input[placeholder*='Address'], input[data-testid*='address']"
        ).first
        addr_input.fill(delivery_address)
        time.sleep(1)
        # Select first suggestion
        suggestion = page.locator("[data-testid*='suggestion'], [class*='suggestion'], li").first
        try:
            suggestion.click(timeout=3000)
        except Exception:
            page.keyboard.press("Enter")
        time.sleep(2)

    # Search for store
    search = page.locator("input[placeholder*='Search'], input[data-testid*='search']").first
    search.fill(store_hint)
    page.keyboard.press("Enter")
    time.sleep(3)

    # Click first store result
    store_link = page.locator("a[href*='/store/']").first
    store_url = store_link.get_attribute("href")
    if not store_url.startswith("http"):
        store_url = "https://www.ubereats.com" + store_url
    return store_url


def _add_items_to_cart(page, store_url: str, items: list[dict]):
    """For each item, either navigate to its URL or search within the store."""
    for item in items:
        item_url = (item.get("url") or "").strip()
        name = item.get("name", "")
        quantity = item.get("quantity", "1")

        try:
            qty = int(str(quantity).split()[0])
        except (ValueError, IndexError):
            qty = 1

        if item_url and item_url.startswith("http"):
            # Navigate directly to item page
            page.goto(item_url, wait_until="domcontentloaded")
        else:
            # Search within store
            page.goto(store_url, wait_until="domcontentloaded")
            time.sleep(2)
            search = page.locator("input[placeholder*='Search'], input[data-testid*='search']").first
            try:
                search.fill(name, timeout=3000)
                page.keyboard.press("Enter")
                time.sleep(2)
                # Click first result
                result = page.locator("li[data-testid*='item'], div[data-testid*='item']").first
                result.click(timeout=4000)
            except Exception:
                # Fall back: click first menu item containing the name
                item_elem = page.locator(f"text={name}").first
                item_elem.click(timeout=4000)

        time.sleep(2)

        # Add to cart (repeat for quantity)
        for _ in range(qty):
            add_btn = page.locator(
                "button:has-text('Add to order'), button:has-text('Add'), "
                "button[data-testid*='add']"
            ).first
            try:
                add_btn.click(timeout=4000)
                time.sleep(1)
            except Exception:
                break

        # Dismiss any modal
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(1)
