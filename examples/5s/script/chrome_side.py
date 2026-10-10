"""What the same page does in Chrome: the reference side of the exchange table.

    python script/chrome_side.py             # 30 seconds, headed, no automation flags
    python script/chrome_side.py --seconds 45

Headed Chrome with ``--enable-automation`` off and ``AutomationControlled`` disabled, because
with either of them Cloudflare refuses this site in real Chrome as well -- which makes it
useless as a reference (`skills/pyv8c-challenge-parity/`, hard rule 1).

It logs the same three witnesses the pyv8c run does, so the two tables can be read side by
side: every response to the challenge hosts, whether ``cf_clearance`` ever arrives, and where
the page ends up.  Bounded: a browser that ignores the close handshake is left to the OS.
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills"
                       / "pyv8c-challenge-parity" / "scripts"))

from harness import _bounded, chrome_path  # noqa: E402

URL = "https://steamdb.info/"

#: The hosts whose answers decide this case; anything else is noise.
WATCHED = ("challenges.cloudflare.com", "steamdb.info")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=30.0, help="seconds to observe")
    arguments = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    seen = []
    playwright = sync_playwright().start()
    try:
        path = chrome_path()
        if not path:
            print("! no system Chrome found: the reference would be Playwright's chromium, "
                  "whose fingerprints differ", file=sys.stderr)
        options = {"headless": False,
                   "ignore_default_args": ["--enable-automation"],
                   "args": ["--disable-blink-features=AutomationControlled"]}
        if path:
            options["executable_path"] = path
        browser = playwright.chromium.launch(**options)
        try:
            context = browser.new_context()
            page = context.new_page()

            def on_response(response):
                url = response.url
                if not any(host in url for host in WATCHED):
                    return
                headers = response.headers
                cookies = headers.get("set-cookie", "")
                names = ", ".join(sorted({part.split("=")[0].strip() for part in cookies.split("\n")
                                          if "=" in part})) if cookies else ""
                #: What Chrome sent, in the same shape the pyv8c table prints: where the two
                #: sides get different answers (400 against 401/200) a header is the suspect.
                #: ``all_headers()``, not ``headers()``: the latter is a documented subset (a
                #: navigation showed three headers and no ``accept-language``), and comparing
                #: against a filtered list would invent differences that are not there.
                sent = "; ".join(f"{name}={str(value)[:160]}"
                                 for name, value in sorted(
                                     dict(response.request.all_headers()).items()))
                #: The answer's own headers, in the same shape the pyv8c table prints them:
                #: the widget continues off ``cf-chl-out``/``cf-chl-out-s``, so a response that
                #: arrives without them is where its flow stops.
                back = "; ".join(f"{name}={str(value)[:80]}"
                                 for name, value in sorted(dict(headers).items()))
                seen.append((response.request.method.upper(), url, response.status,
                             int(headers.get("content-length") or -1), names, sent, back))

            page.on("response", on_response)
            started = time.time()
            try:
                page.goto(URL, wait_until="load", timeout=60000)
            except BaseException as error:  # noqa: BLE001 - a bot wall need not finish loading
                print(f"goto: {type(error).__name__}: {str(error)[:100]}")
            deadline = time.time() + arguments.seconds
            while time.time() < deadline:
                page.wait_for_timeout(500)
                if any(cookie.get("name") == "cf_clearance" for cookie in context.cookies()):
                    print("cf_clearance arrived early")
                    break

            print(f"exchanges ({len(seen)}):")
            for method, url, status, size, names, sent, back in seen:
                print(f"    {method:4} {url} -> {status} {size}B"
                      + (f" set-cookie: {names}" if names else ""))
                if sent:
                    print(f"         hdr : {sent}")
                if back:
                    print(f"         back: {back}")
            jar = context.cookies()
            print(f"cookies ({len(jar)}): "
                  f"{', '.join(sorted({entry['name'] for entry in jar})) or '(none)'}")
            for entry in jar:
                print(f"    {entry['name']}@{entry['domain']}{entry['path']} = "
                      f"{str(entry['value'])[:24]}")
            state = {"url": page.url[:120], "title": page.title()[:80],
                     "body": " ".join((page.inner_text("body") or "").split())[:200]}
            print(f"page: {json.dumps(state, ensure_ascii=False)}")
            print(f"ran {time.time() - started:.1f}s")
            return 0
        finally:
            _bounded(browser.close, "browser.close()")
    finally:
        _bounded(playwright.stop, "playwright.stop()")


if __name__ == "__main__":
    threading.current_thread().name = "chrome-side"
    raise SystemExit(main())
