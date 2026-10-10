"""What the native layer already offers for promises, measured against the JS-helper route.

pyv8c builds every promise by evaluating JavaScript (``foundation/scripts.py``: ``make_promise``)
and settles it through a cached ``JSFunction``.  That is not how Chromium does it -- the binding
layer holds a ``v8::Promise::Resolver`` and calls ``Resolve``/``Reject`` on it
(``v8-promise.h``: ``Resolver::New``, ``GetPromise``, ``Resolve``, ``Reject``) -- and pyv8's own
``JSPromise`` already does exactly that in C++ (``src/handles/jspromise.cpp``).  What is missing
is only the *pending* half: a resolver object Python can hold and settle later.

This probe answers two questions with numbers instead of taste:

* how much the JS-helper route costs per settle, against ``pyv8.JSPromise(resolve=...)``;
* whether a Python exception can be turned into a JavaScript error without evaluating a factory
  (``Context.to_js``), which is what ``js_error_value`` currently needs a helper for.

    python script/promise_native.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills"
                       / "pyv8c-challenge-parity" / "scripts"))

ROUNDS = 2000


def main():
    import traceback

    from loguru import logger

    from pyv8c import Browser

    #: The engine logs every dispatch at DEBUG on stderr; a probe's own numbers have to be
    #: findable, so the default sink goes before anything else runs.
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
            context = page.context
            scripts = page.realm.scripts

            #: (a) the route in use: a cached JSFunction from the realm's helpers
            started = time.perf_counter()
            for _ in range(ROUNDS):
                scripts.promise_resolve(1)
            via_helper = time.perf_counter() - started

            #: (b) the native route: JSPromise builds and settles with a v8::Promise::Resolver,
            #: but only where a V8 context is already active on this thread -- from the outside
            #: it answers "JSPromise requires an active V8 Context", which is the whole reason
            #: the engine goes through ``exec_js`` today.
            import pyv8
            try:
                pyv8.JSPromise(resolve=1)
                native = "usable from here"
            except BaseException as error:  # noqa: BLE001
                native = f"{type(error).__name__}: {error}"

            print(f"  promise_resolve x{ROUNDS}: {via_helper * 1000:.1f}ms "
                  f"({via_helper / ROUNDS * 1e6:.1f}us each)")
            print(f"  pyv8.JSPromise from outside a realm -> {native}")
            #: ``Context.to_js`` (the native Python-exception-to-JS-error path) is worth
            #: checking before writing the resolver: it would retire the two factory helpers.
            print(f"  has Context.to_js: {hasattr(context, 'to_js')}")
        except BaseException:
            #: Printed *here*: the ``finally`` below ends in ``os._exit``, which would otherwise
            #: take the traceback with it -- the very trap that made this probe look silent.
            traceback.print_exc()
            code = 1
        sys.stdout.flush()
    finally:
        import os
        import threading
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(10.0)
        sys.stdout.flush()
        os._exit(code)


def _as_reference(value):
    """A JavaScript expression for an already-bound value (a plain number is enough here)."""
    return str(value)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        #: ``main`` ends in ``os._exit`` so a wedged realm cannot outlive the probe; that would
        #: also take the traceback with it, so the failure is printed before that exit runs.
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        raise SystemExit(1)
