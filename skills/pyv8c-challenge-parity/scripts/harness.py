"""Run one page in pyv8c and in headed Chrome, and diff what a probe answers.

    python harness.py --list                          # the probes that ship here
    python harness.py --probe ua --probe rect         # built-in fixtures, no site needed
    python harness.py --url https://example.com --probe layout --probe style
    python harness.py --url https://example.com --probe all --seconds 25
    python harness.py --url https://example.com --probe layout --side here   # pyv8c only
    python harness.py --url https://example.com --probe style \
        --wait-for "!!document.querySelector('#app')"

A probe is one JavaScript expression (see ``probes.py``). Both engines get the same
page, the same viewport and the same settling time, so a difference in the answers is
a difference in behaviour rather than in conditions. A probe that carries a fixture
runs offline against it, which is how a finding on a live site becomes a permanent
regression case that needs no access to that site.
"""
import argparse
import json
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probes import PROBES  # noqa: E402

FIXTURE_URL = "https://probe.test/{}.html"
VIEWPORT = {"width": 1920, "height": 1080}

#: Where a real Chrome is, in the order worth trying. ``CHROME_PATH`` wins.
CHROME_CANDIDATES = (
    os.environ.get("CHROME_PATH"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def chrome_path():
    for candidate in CHROME_CANDIDATES:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def pump(page, seconds=0.0, until=None):
    """Let the queue turn, for *seconds* or until *until* answers true.

    ``run_until_idle(timeout=N)`` raises while a long page task runs, so the pump
    never blocks: it asks for no waiting, sleeps a little, and checks the deadline.
    pyv8c's JS errors derive from ``BaseException``, so both are caught here.
    """
    deadline = time.time() + seconds
    while True:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001
            pass
        if until is not None:
            try:
                if page.evaluate(until):
                    return True
            except BaseException:  # noqa: BLE001
                pass
        if time.time() >= deadline:
            return until is None
        time.sleep(0.2)


def snapshot(page, probe):
    """The probe's answer, or the error it raised, as plain data."""
    try:
        return page.evaluate(probe)
    except BaseException as error:  # noqa: BLE001
        return {"__error": f"{type(error).__name__}: {str(error)[:220]}"}


def target(name, spec, arguments):
    """``(url, page_html)``: a probe with a fixture serves itself, else ``--url``."""
    fixture = spec.get("fixture")
    if fixture is not None:
        return FIXTURE_URL.format(name), fixture[0]
    if not arguments.url:
        raise SystemExit(f"probe {name!r} has no fixture: pass --url <page>")
    return arguments.url, None


def run_here(url, page_html, probe, settle, wait_for):
    """pyv8c's answer, with the viewport it reported (Chrome is matched to that)."""
    from pyv8c import Browser

    #: A probe measures answers, not noise.  The engine logs through loguru, so ``disable`` is
    #: what silences it -- ``logging.getLogger`` does nothing here.
    try:
        from loguru import logger as _loguru

        _loguru.disable("pyv8c")
    except ImportError:  # a browser that never logs does not need loguru
        logging.getLogger("pyv8c").setLevel(logging.WARNING)
    options = {"stub_logging": "off", "threaded_loaders": True}
    if page_html is not None:
        def load(url, request):
            """Serve the probe page, and only for a fetchable scheme.

            Answering ``data:`` (or any other non-http URL) with the page itself would feed a
            frame the page that created it, so the probe would recurse: that looked exactly
            like a wedge caused by the engine until it was bisected (2026-10-06).
            """
            if not str(url).startswith(("http://", "https://")):
                return None
            return {"body": page_html, "headers": {"content-type": "text/html"}}

        options.update(real_network=False, resource_loader=load)
    browser = Browser(**options)
    try:
        page = browser.new_context().new_page()
        page.goto(url)
        pump(page, settle, wait_for)
        answer = snapshot(page, probe)
        try:
            viewport = {"width": int(page.evaluate("window.innerWidth")),
                        "height": int(page.evaluate("window.innerHeight"))}
        except Exception:  # noqa: BLE001
            viewport = None
        return answer, viewport
    finally:
        browser.close()


#: Wait this long for a close handshake, then leave that Chrome to the OS and carry on.
CLOSE_SECONDS = 10.0


def _bounded(action, what, seconds=CLOSE_SECONDS):
    """Run *action* on a daemon thread, giving up when it does not finish in time."""
    done = threading.Event()

    def run():
        try:
            action()
        except BaseException:  # noqa: BLE001 - a browser already gone is not a failure
            pass
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    if not done.wait(seconds):
        print(f"! {what} did not finish in {seconds:g}s; leaving that Chrome to the OS",
              file=sys.stderr)


def _ask_page(url, page_html, probe="window.__out", seconds=3.0, wait_for=None):
    """Both answers for one page in one call: ``(pyv8c, Chrome)``.

    The smallest thing that can support a conclusion: the same bytes asked the same question
    on both sides, with no third-party traffic in the way.
    """
    mine, viewport = run_here(url, page_html, probe, seconds, wait_for)
    theirs = run_chrome(url, page_html, probe, seconds, wait_for, viewport)
    return mine, theirs


def run_chrome(url, page_html, probe, settle, wait_for, viewport):
    """Chrome's answer, on the viewport pyv8c reported when there is one."""
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        path = chrome_path()
        #: With ``--enable-automation`` on, Cloudflare refuses SteamDB in real Chrome too,
        #: which makes it useless as a reference.
        options = {"headless": False,
                   "ignore_default_args": ["--enable-automation"],
                   "args": ["--disable-blink-features=AutomationControlled"]}
        if path:
            options["executable_path"] = path
        else:
            print("! no system Chrome found: falling back to Playwright's chromium, "
                  "whose user agent and fingerprints differ", file=sys.stderr)
        browser = playwright.chromium.launch(**options)
        try:
            page = browser.new_page(viewport=viewport or VIEWPORT)
            if page_html is not None:
                page.route("**/*", lambda route: route.fulfill(
                    status=200, body=page_html, headers={"content-type": "text/html"}))
            page.goto(url, wait_until="load", timeout=60000)
            if wait_for:
                deadline = time.time() + settle
                while time.time() < deadline:
                    try:
                        if page.evaluate(wait_for):
                            break
                    except Exception:  # noqa: BLE001
                        pass
                    page.wait_for_timeout(200)
            else:
                page.wait_for_timeout(settle * 1000)
            return snapshot(page, probe)
        finally:
            _bounded(browser.close, "browser.close()")
    finally:
        _bounded(playwright.stop, "playwright.stop()")


def diff(mine, theirs):
    """Field by field, marking what differs; answers the differing keys."""
    if not isinstance(mine, dict) or not isinstance(theirs, dict):
        if mine == theirs:
            print("    identical")
            return []
        print(f"    here   {json.dumps(mine, ensure_ascii=False)[:160]}")
        print(f"    chrome {json.dumps(theirs, ensure_ascii=False)[:160]}")
        return ["<scalar>"]
    differing = []
    for key in sorted(set(mine) | set(theirs)):
        left, right = mine.get(key), theirs.get(key)
        if left == right:
            continue
        differing.append(key)
        print(f"!= {key:22} here {json.dumps(left, ensure_ascii=False)[:120]}")
        print(f"   {'':22} chr  {json.dumps(right, ensure_ascii=False)[:120]}")
    if not differing:
        print("    no differences")
    return differing


def describe():
    print("probes:")
    for name, spec in PROBES.items():
        needs = "fixture" if spec.get("fixture") else "needs --url"
        print(f"  {name:10} {needs:10} {spec['note']}")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="the page to run (probes with a fixture ignore it)")
    parser.add_argument("--probe", action="append", default=[],
                        help="probe name (repeatable, or `all`)")
    parser.add_argument("--side", choices=("here", "chrome", "both"), default="both")
    parser.add_argument("--seconds", type=float, default=6.0,
                        help="how long to let the page settle (a fixture uses its own)")
    parser.add_argument("--wait-for", help="a JS expression to wait for, instead of a fixed time")
    parser.add_argument("--out", help="write every answer to this JSON file")
    parser.add_argument("--list", action="store_true", help="list the probes and exit")
    arguments = parser.parse_args(argv)

    if arguments.list or not arguments.probe:
        describe()
        return 0

    names = list(PROBES) if "all" in arguments.probe else arguments.probe
    answers, differences = {}, 0
    for name in names:
        spec = PROBES.get(name)
        if spec is None:
            print(f"=== {name}: unknown probe (try --list)")
            continue
        url, page_html = target(name, spec, arguments)
        settle = spec["fixture"][1] if spec.get("fixture") else arguments.seconds
        print(f"=== {name}: {spec['note']}")
        entry = answers.setdefault(name, {})
        viewport = None
        if arguments.side in ("here", "both"):
            mine, viewport = run_here(url, page_html, spec["probe"], settle, arguments.wait_for)
            entry["here"] = mine
            entry["viewport"] = viewport
            print(f"    here   {json.dumps(mine, ensure_ascii=False)[:150]}")
        if arguments.side in ("chrome", "both"):
            theirs = run_chrome(url, page_html, spec["probe"], settle,
                                arguments.wait_for, viewport)
            entry["chrome"] = theirs
            print(f"    chrome {json.dumps(theirs, ensure_ascii=False)[:150]}")
        if arguments.side == "both" and diff(entry["here"], entry["chrome"]):
            differences += 1

    if arguments.out:
        with open(arguments.out, "w", encoding="utf-8") as handle:
            json.dump(answers, handle, ensure_ascii=False, indent=1)
        print("->", arguments.out)
    print(f"== probes with differences: {differences}/{len(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
