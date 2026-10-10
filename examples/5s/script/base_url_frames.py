"""A root-relative URL inside a framed document: what each engine asks for, and what it sees.

    python script/base_url_frames.py

The warning this reproduces (2026-10-10, a real steamdb run):

    WARNING pyv8c.platform.lifecycle:dispatch_script_error - a script's failure was the
    host's own: : is not a scheme a page loads, so nothing was requested
    </cdn-cgi/challenge-platform/scripts/jsd/main.js>

The scheme in that message is *empty*: the challenge's own ``jsd`` script carries a
root-relative ``src``, and the document that asked for it has no address of its own.  A browser
takes the base URL from the document that created it, so ``page_base_url`` follows the same
``parent_page`` walk ``page_secure_context`` already does -- which aligned the plain ``srcdoc``
case (B).

Two shapes stayed apart, and this tool is what decides how to read them:

    A  ``srcdoc`` + ``sandbox="allow-scripts"``   Chrome asks for nothing; we ask for A.js
    C  ``about:blank`` the parent writes into     Chrome asks for C.js; we ask for nothing

Both sides run the same page: the frame in A reports its own ``document.baseURI`` and whether
its script fired ``load`` or ``error``; the parent reads C's document back (same-origin) for
the markup it wrote, the script count, and whether the script ran.  No third-party traffic.
"""
import json
import sys
import time

SKILL = r"D:\src\pyv8env\pyv8c\skills\pyv8c-challenge-parity\scripts"
sys.path.insert(0, SKILL)

URL = "https://probe.test/base_url.html"


def _page():
    """The probe page: three shapes, each asking for its own path, plus the readings."""
    #: The inline script sits inside a ``<body>``: without one, Chrome's ``document.body`` is
    #: still null when the script runs, so ``appendChild`` throws and *nothing* is requested --
    #: which read exactly like "Chrome drops the sandboxed frame's script" until it was fixed
    #: here.  A probe that changes the answer it measures is the skill's first known trap.
    markup_a = (
        "'<body><script>window.addEventListener(\"load\",function(){"
        "parent.postMessage(\"A|docload|\"+String(document.baseURI)+\"|\"+String(location.href),\"*\");});"
        "var s=document.createElement(\"script\");s.src=\"/A.js\";"
        "parent.postMessage(\"A|src|\"+String(s.src),\"*\");"
        "s.onload=function(){parent.postMessage(\"A|load|\"+String(s.src),\"*\");};"
        "s.onerror=function(){parent.postMessage(\"A|error|\"+String(s.src),\"*\");};"
        "document.body.appendChild(s);"
        "</scr' + 'ipt>'"
    )
    markup_b = "'<script src=\"/B.js\"></scr' + 'ipt>'"
    body = (
        "window.__say = [];"
        "window.addEventListener('message', function (event) { window.__say.push(String(event.data)); });"
        "var sandboxed = document.createElement('iframe');"
        "sandboxed.setAttribute('sandbox', 'allow-scripts');"
        f"sandboxed.setAttribute('srcdoc', {markup_a});"
        "document.body.appendChild(sandboxed);"
        "var plain = document.createElement('iframe');"
        f"plain.setAttribute('srcdoc', {markup_b});"
        "document.body.appendChild(plain);"
        "var blank = document.createElement('iframe');"
        "document.body.appendChild(blank);"
        "try {"
        "  blank.contentDocument.write('<script src=\"/C.js\"></scr' + 'ipt>');"
        "  blank.contentDocument.close();"
        "} catch (error) { window.__c_error = error.name + ': ' + String(error.message); }"
        "window.__out = {base: String(document.baseURI), href: String(location.href)};"
        "setTimeout(function () {"
        "  window.__out.say = window.__say;"
        "  window.__out.c_error = String(window.__c_error);"
        "  try {"
        "    window.__out.c = {'url': String(blank.contentDocument.URL),"
        "      'scripts': blank.contentDocument.scripts.length,"
        "      'ran': String(blank.contentWindow.__c_ran),"
        "      'html': String(blank.contentDocument.body ? blank.contentDocument.body.innerHTML : '')"
        "              .slice(0, 120)};"
        "  } catch (error) { window.__out.c = 'unreadable: ' + error.name; }"
        "  try {"
        "    window.__out.b = {scripts: plain.contentDocument ? plain.contentDocument.scripts.length"
        "                      : 'no document', ran: String(plain.contentWindow.__b_ran)};"
        "  } catch (error) { window.__out.b = 'unreadable: ' + error.name; }"
        "}, 900);"
    )
    return ("<!doctype html><html><body><script>" + body
            + "</" + "script></body></html>")


PAGE = _page()


def _script_body(url):
    """The JS each shape loads: it marks its own window, so a load can be seen, not inferred."""
    for shape in ("A", "B", "C"):
        if f"/{shape}.js" in str(url):
            return f"window.__{shape.lower()}_ran = 1;"
    return "/* a script */"


def here():
    """pyv8c: what its loader was asked for, and what the page says about itself."""
    from loguru import logger

    from pyv8c import Browser

    logger.remove()
    asked = []

    def load(url, request):
        asked.append(str(url))
        if str(url).endswith(".js"):
            return {"body": _script_body(url), "headers": {"content-type": "text/javascript"}}
        return {"body": PAGE, "headers": {"content-type": "text/html"}}

    browser = Browser(stub_logging="off", real_network=False, resource_loader=load,
                      threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        page.goto(URL)
        deadline = time.time() + 3.5
        while time.time() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except BaseException:  # noqa: BLE001
                pass
            time.sleep(0.2)
        try:
            answer = page.evaluate("window.__out")
        except BaseException as error:  # noqa: BLE001
            answer = {"__error": type(error).__name__}
        #: ``window.__out`` came back ``undefined`` twice while the frames clearly ran, so the
        #: probe reports how the same window answers before claiming anything about the page.
        diagnostics = {}
        for label, expression in (("one plus one", "1 + 1"),
                                  ("readyState", "document.readyState"),
                                  ("scripts", "document.scripts.length"),
                                  ("typeof __out", "typeof window.__out"),
                                  ("href", "String(location.href)")):
            try:
                diagnostics[label] = str(page.evaluate(expression))[:90]
            except BaseException as error:  # noqa: BLE001
                diagnostics[label] = f"<{type(error).__name__}>"
        return asked, {"answer": answer, "diagnostics": diagnostics}
    finally:
        browser.close()


def there():
    """Chrome: the same page, its requests, and its own reading of the same names."""
    from playwright.sync_api import sync_playwright

    from harness import chrome_path

    asked = []
    with sync_playwright() as playwright:
        options = {"headless": False, "ignore_default_args": ["--enable-automation"],
                   "args": ["--disable-blink-features=AutomationControlled"]}
        path = chrome_path()
        if path:
            options["executable_path"] = path
        browser = playwright.chromium.launch(**options)
        try:
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.on("request", lambda request: asked.append(request.url))

            def route(handler):
                url = handler.request.url
                if url.endswith(".js"):
                    handler.fulfill(status=200, body=_script_body(url),
                                    headers={"content-type": "text/javascript"})
                else:
                    handler.fulfill(status=200, body=PAGE, headers={"content-type": "text/html"})

            page.route("**/*", route)
            page.goto(URL, wait_until="load", timeout=60000)
            page.wait_for_timeout(3000)
            try:
                answer = page.evaluate("window.__out")
            except BaseException as error:  # noqa: BLE001
                answer = {"__error": type(error).__name__}
            return asked, answer
        finally:
            browser.close()


def show(mine, theirs, shape):
    wanted = [url for url in theirs if f"/{shape}.js" in url]
    got = [url for url in mine if f"/{shape}.js" in url]
    verdict = "same" if wanted == got else "DIFFERENT"
    print(f"    {shape}.js  chrome={wanted}  pyv8c={got}  -> {verdict}")


if __name__ == "__main__":
    mine, mine_out = here()
    theirs, theirs_out = there()
    print("=== pyv8c asked for")
    for url in mine:
        print(f"    {url[:110]}")
    print("=== chrome asked for")
    for url in theirs:
        print(f"    {url[:110]}")
    print("=== per shape (A sandboxed srcdoc / B plain srcdoc / C about:blank)")
    for shape in ("A", "B", "C"):
        show(mine, theirs, shape)
    print("=== what each side saw")
    #: ``str`` rather than ``json``: an engine answering ``undefined`` is itself a reading.
    print(f"    pyv8c  {str(mine_out)[:900]}")
    print(f"    chrome {str(theirs_out)[:900]}")
