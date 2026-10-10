"""Who reads the globals the challenge hammers, and from which script.

    python script/trace_reflect.py --seconds 30 [--names Reflect,String,isNaN] [--hits 1]

``5s.py --trace`` shows ``[FRAME] getter: Window.Reflect`` ~130k times in 15 s.  The frame's
own JS sees ``Reflect`` as a **data property**, so those reads are not what they look like;
and a trace callback carries no source location, so it cannot name the reader either.

Two read-only hooks answer it:

* ``ScriptController.evaluate`` is the door every page script goes through, so wrapping it
  puts the running script's name in a thread local -- the trace callback runs on that same
  thread, so each read can be attributed to a file;
* the callback itself counts every traced access by name, and prints the Python stack behind
  the first reads of the names asked for.

The engine's own loguru sink is removed: this probe's report is the finding, not the log.
"""
import argparse
import collections
import os
import sys
import threading
import time
import traceback

URL = "https://steamdb.info/"
WATCHED = ("Reflect", "String", "isNaN", "Array")

#: Which script each realm's thread is inside right now (``evaluate`` nests).
RUNNING = threading.local()


def install_script_witness():
    """Track the running script per thread, without changing what runs."""
    from pyv8c.platform.scripting import ScriptController

    original = ScriptController.evaluate

    def wrapped(self, source, filename=None):
        previous = getattr(RUNNING, "script", None)
        RUNNING.script = str(filename or "(inline)")[:70]
        try:
            return original(self, source, filename=filename)
        finally:
            RUNNING.script = previous

    ScriptController.evaluate = wrapped


def make_callback(watched, points, stack_limit):
    names = collections.Counter()
    readers = collections.Counter()
    per_realm = collections.Counter()
    state = {"reads": 0}
    points = sorted({int(point) for point in points})

    def callback(event, receiver, name, arguments, value, error, is_construct, ctx_type):
        key = f"{event}:{name}"
        names[key] += 1
        if key.startswith("get:") and name in watched:
            readers[f"{getattr(RUNNING, 'script', '?')}  ::  {name}"] += 1
            per_realm[f"{ctx_type} {name}"] += 1
            state["reads"] += 1
            if points and state["reads"] >= points[0]:
                where = points.pop(0)
                print(f"=== read #{state['reads']} (asked {where}) [{ctx_type}] {name} on "
                      f"{type(receiver).__module__}.{type(receiver).__name__} "
                      f"script={getattr(RUNNING, 'script', '?')}", flush=True)
                for line in traceback.format_stack(limit=stack_limit):
                    print("    " + line.strip().replace("\n", " | ")[:190], flush=True)
        return None

    return callback, names, readers, per_realm


def browse(seconds, watched, points, stack_limit):
    from loguru import logger

    from pyv8c import Browser

    logger.remove()
    install_script_witness()
    callback, names, readers, per_realm = make_callback(watched, points, stack_limit)
    browser = Browser(stub_logging="off", trace=True, traceable=True, trace_callback=callback,
                      threaded_loaders=True)
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
        print(f"=== {seconds:.0f}s  watched reads={sum(readers.values())}")
        print("--- reads of the watched names, by script")
        for key, count in readers.most_common(12):
            print(f"    {count:8d}  {key}")
        print("--- same reads, by realm")
        for key, count in per_realm.most_common(8):
            print(f"    {count:8d}  {key}")
        print("--- top traced accesses overall")
        for key, count in names.most_common(12):
            print(f"    {count:8d}  {key}")
        sys.stdout.flush()
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(5.0)
        os._exit(0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--names", default=",".join(WATCHED))
    parser.add_argument("--at", default="1,50000,300000",
                        help="report the stack at these read counts (of the watched names)")
    parser.add_argument("--stack", type=int, default=24)
    arguments = parser.parse_args(argv)
    points = [int(part) for part in arguments.at.split(",") if part.strip()]
    browse(arguments.seconds,
           tuple(part.strip() for part in arguments.names.split(",") if part.strip()),
           points, arguments.stack)
    return 0


if __name__ == "__main__":
    sys.exit(main())
