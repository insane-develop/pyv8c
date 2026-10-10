"""The new native resolver, checked from the three places an engine settles a promise.

``pyv8.PromiseResolver(context)`` wraps ``v8::Promise::Resolver`` (``Resolver::New``,
``GetPromise``, ``Resolve``, ``Reject``) and keeps its Context alive, so:

1. it can be created **from outside the realm** -- the main thread -- which
   ``pyv8.JSPromise`` cannot do (it answers "requires an active V8 Context");
2. the promise it hands out is a real one, observable from JavaScript;
3. settling it later reaches the page;
4. and once the realm is closed every method names the realm instead of failing deeper.

    python script/promise_resolver.py
"""
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills"
                       / "pyv8c-challenge-parity" / "scripts"))


def main():
    import pyv8
    from loguru import logger

    from pyv8c import Browser

    logger.remove()

    def load(url, request):
        if not str(url).startswith(("http://", "https://")):
            return None
        return {"body": "<!doctype html><body>probe", "headers": {"content-type": "text/html"}}

    browser = Browser(stub_logging="off", threaded_loaders=True, real_network=False,
                      resource_loader=load)
    code = 0
    try:
        try:
            page = browser.new_context().new_page()
            page.set_content("<!doctype html><body>probe", url="https://probe.test/promise.html")

            #: (1) outside the realm, and (2) a promise the page can see
            resolver = pyv8.PromiseResolver(page.context)
            resolver2 = pyv8.PromiseResolver(page.context)
            page.context.define_global("__from_python", resolver.promise())
            page.context.define_global("__from_python2", resolver2.promise())
            print("  promise visible : "
                  + json.dumps(page.evaluate(
                      "[__from_python instanceof Promise, Object.prototype.toString.call(__from_python)]")))

            #: watch from JavaScript, then settle from Python
            page.evaluate("""
                window.__seen = [];
                __from_python.then(function (value) { window.__seen.push(['resolved', value]); },
                                   function (error) { window.__seen.push(['rejected', error.name]); });
                __from_python2.then(function (value) { window.__seen.push(['resolved2', value]); },
                                    function (error) { window.__seen.push(['rejected2', error.name]); });
            """)
            resolver.resolve(42)
            resolver2.reject(ValueError("from python"))
            pump(page)
            print("  settled from python: " + json.dumps(page.evaluate("JSON.stringify(window.__seen)")))

            #: (4) the realm closes, and the handle says so
            closer = threading.Thread(target=browser.close, daemon=True)
            closer.start()
            closer.join(10.0)
            try:
                resolver.resolve(1)
                print("  after close    : NO ERROR (wrong)")
            except BaseException as error:  # noqa: BLE001
                print(f"  after close    : {type(error).__name__}: {error}")
            try:
                resolver.promise()
            except BaseException as error:  # noqa: BLE001
                print(f"  after close    : {type(error).__name__}: {error}")
        except BaseException:
            traceback.print_exc()
            code = 1
        sys.stdout.flush()
    finally:
        sys.stdout.flush()
        os._exit(code)


def pump(page, seconds=0.6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001
            pass
        time.sleep(0.02)


if __name__ == "__main__":
    raise SystemExit(main())
