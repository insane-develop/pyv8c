"""The handler that will not return: dump the source of any callback that overruns.

    python script/slow_handlers.py --seconds 40 [--over 1.0] [--max 20]

The trace says the challenge reads ``String``/``isNaN``/``Reflect`` ~670k times each and the
reader is *not* inside ``ScriptController.evaluate``: a stack sample lands in
``xhr.py:97 -> events.py:_invoke -> _js_call``, i.e. one invocation of a page's XHR
``readystatechange`` handler that sits there.  A handler is a JS value with no Python-side
source, but ``Function.prototype.toString`` still answers, so timing ``_js_call`` and dumping
what overran names the code without editing the page.

Read-only: the wrapped call is the original one, and the dump is what the page already holds.
"""
import argparse
import os
import pathlib
import sys
import threading
import time

URL = "https://steamdb.info/"
DEFAULT_DIR = pathlib.Path(__file__).resolve().parent / "cf_frame"


def source_of(fn):
    """A JS function's own text, or why we could not get it."""
    import pyv8

    try:
        return str(fn["toString"].call(fn))
    except BaseException as error:  # noqa: BLE001
        return f"// no source: {type(error).__name__}: {str(error)[:160]}"


def install(directory, over, most):
    from pyv8c.web.dom import events

    original = events._js_call
    state = {"dumped": 0, "seen": 0}

    def wrapped(fn, this, *args):
        started = time.perf_counter()
        try:
            return original(fn, this, *args)
        finally:
            seconds = time.perf_counter() - started
            state["seen"] += 1
            if seconds >= over and state["dumped"] < most:
                state["dumped"] += 1
                text = source_of(fn)
                name = type(fn).__name__
                path = directory / f"handler-{state['dumped']:02d}-{seconds:.1f}s.js"
                path.write_text(f"// {name} held the realm {seconds:.2f}s\n"
                                f"// event: {args[0]!r}\n\n{text}", encoding="utf-8")
                print(f"    {path.name:44} {len(text):7d}B  ({state['seen']} calls so far,"
                      f" {seconds:.2f}s)", flush=True)

    events._js_call = wrapped
    return state


def browse(seconds, directory, over, most):
    from loguru import logger

    from pyv8c import Browser

    logger.remove()
    directory.mkdir(parents=True, exist_ok=True)
    state = install(directory, over, most)
    browser = Browser(stub_logging="off", threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        try:
            page.goto(URL)
        except BaseException as error:  # noqa: BLE001
            print(f"goto: {type(error).__name__}: {str(error)[:100]}")
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except BaseException:  # noqa: BLE001
                pass
            time.sleep(0.2)
        print(f"=== {seconds:.0f}s: {state['seen']} callback(s), {state['dumped']} dumped")
        sys.stdout.flush()
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(5.0)
        os._exit(0 if state["dumped"] else 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=40.0)
    parser.add_argument("--dir", type=pathlib.Path, default=DEFAULT_DIR)
    parser.add_argument("--over", type=float, default=1.0, help="seconds a callback may hold")
    parser.add_argument("--max", type=int, default=20, help="how many handlers to dump")
    arguments = parser.parse_args(argv)
    browse(arguments.seconds, arguments.dir, arguments.over, arguments.max)
    return 0


if __name__ == "__main__":
    sys.exit(main())
