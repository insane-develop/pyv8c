"""Shared helpers for the px3 analysis scripts.

The challenge URL lives in the runner (``../px3.py``), so every script here reads
it from there and a fresh capture gets a fresh uuid with one edit.
"""
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from px3 import URL  # noqa: E402  the runner owns the challenge URL

#: Chrome's executable, and the window the environment profile declares.
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
VIEWPORT = {"width": 1920, "height": 1080}


def launch(playwright, **options):
    """Chrome, headed: the reference both engines are compared against."""
    options.setdefault("headless", False)
    options.setdefault("executable_path", CHROME)
    return playwright.chromium.launch(**options)


def chrome_page(browser, **options):
    options.setdefault("viewport", VIEWPORT)
    return browser.new_page(**options)


def offline(body, kind="text/html"):
    """A loader that answers every request with one document."""
    def load(url, request):
        return {"body": body, "headers": {"content-type": kind}}
    return load


def routes(pages):
    """A Playwright route handler serving ``{suffix: (body, content type)}``."""
    def handler(route):
        target = route.request.url
        for suffix, (body, kind) in pages.items():
            if target.endswith(suffix):
                route.fulfill(status=200, body=body, headers={"content-type": kind})
                return
        route.fulfill(status=200, body="", headers={"content-type": "text/plain"})
    return handler


def pump(page, seconds=0.0):
    """Let the queue turn for *seconds*.

    A running challenge keeps its own timers going, so the page need not reach
    idle; a realm that is busy mid-task is left to finish and retried.
    """
    deadline = time.time() + seconds
    while True:
        try:
            page.run_until_idle(timeout=0)
        except Exception:  # noqa: BLE001
            pass
        if time.time() >= deadline:
            return
        time.sleep(0.2)


@contextmanager
def here(url, seconds=10.0, loader=None):
    """A pyv8c page, settled for *seconds*."""
    from pyv8c import Browser
    options = {"stub_logging": "off", "threaded_loaders": True}
    if loader is not None:
        options.update(real_network=False, resource_loader=loader)
    browser = Browser(**options)
    try:
        page = browser.new_context().new_page()
        page.goto(url)
        pump(page, seconds)
        yield page
    finally:
        browser.close()


@contextmanager
def there(url, seconds=10.0, route=None):
    """A Chrome page, settled for *seconds*."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = launch(playwright)
        try:
            page = chrome_page(browser)
            if route is not None:
                page.route("**/*", route)
            page.goto(url, wait_until="load", timeout=60000)
            page.wait_for_timeout(seconds * 1000)
            yield page
        finally:
            browser.close()


def snapshot(page, probe):
    """The probe's answer, or the error it raised, as plain data."""
    try:
        return page.evaluate(probe)
    except Exception as error:  # noqa: BLE001
        return {"error": f"{type(error).__name__}: {str(error)[:160]}"}


def compare(state_here, state_chrome, keys=None):
    """Print the two answers field by field, marking what differs."""
    keys = keys or sorted(set(state_here) | set(state_chrome))
    differences = []
    for key in keys:
        mine = state_here.get(key)
        theirs = state_chrome.get(key)
        same = mine == theirs
        if not same:
            differences.append(key)
        flag = "  " if same else "!="
        print(f"{flag} {key:14} here {json.dumps(mine, ensure_ascii=False)[:150]}")
        if not same:
            print(f"   {'':14} chr  {json.dumps(theirs, ensure_ascii=False)[:150]}")
    print(f"-- differing: {differences or 'none'}")
    return differences
