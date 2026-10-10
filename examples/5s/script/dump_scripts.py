"""Put every page script the engine runs on disk, so the challenge can be read offline.

    python script/dump_scripts.py --seconds 30 [--dir script/cf_frame] [--max 40]

The trace names what the page *reads*; the source says *why*.  ``ScriptController.evaluate``
is the one door every script of every realm goes through (inline, external, frame, worker
blob), so wrapping it captures the whole challenge without editing a response or touching
the page.  Read-only: the original still runs, unchanged.

Files land as ``NN-<realm>-<n>.js`` beside the case, which is the layout the skill's
``references/js-reverse.md`` reads from.
"""
import argparse
import os
import pathlib
import re
import sys
import threading
import time

URL = "https://steamdb.info/"
DEFAULT_DIR = pathlib.Path(__file__).resolve().parent / "cf_frame"


def safe(name, limit=48):
    """A filename that keeps enough of the URL to recognise the script."""
    tail = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "inline"))[-limit:]
    return tail or "inline"


def install(directory, most, biggest):
    """Write every script that reaches the engine, before it runs."""
    from pyv8c.platform.scripting import ScriptController

    original = ScriptController.evaluate
    state = {"count": 0, "bytes": 0, "skipped": 0}

    def wrapped(self, source, filename=None):
        if state["count"] < most:
            text = source if isinstance(source, str) else str(source)
            if len(text) > biggest:
                state["skipped"] += 1
                print(f"    (skip {len(text)}B: {safe(filename, 60)})", flush=True)
            else:
                state["count"] += 1
                state["bytes"] += len(text)
                url = str(getattr(getattr(self, "page", None), "url", "") or "")
                label = f"{safe(filename, 34)}"
                path = directory / f"{state['count']:02d}-{label}.js"
                path.write_text(f"// {filename or '(inline)'}\n// page: {url[:120]}\n\n{text}",
                                encoding="utf-8")
                print(f"    {path.name:58} {len(text):8d}B", flush=True)
        return original(self, source, filename=filename)

    ScriptController.evaluate = wrapped


def browse(seconds, directory, most, biggest):
    from loguru import logger

    from pyv8c import Browser

    logger.remove()
    directory.mkdir(parents=True, exist_ok=True)
    install(directory, most, biggest)
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
        print(f"=== {directory}: {sorted(p.name for p in directory.iterdir())[:4]} ...")
        sys.stdout.flush()
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(5.0)
        os._exit(0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--dir", type=pathlib.Path, default=DEFAULT_DIR)
    parser.add_argument("--max", type=int, default=40, help="how many scripts to keep")
    parser.add_argument("--biggest", type=int, default=2_000_000, help="largest script to keep")
    arguments = parser.parse_args(argv)
    browse(arguments.seconds, arguments.dir, arguments.max, arguments.biggest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
