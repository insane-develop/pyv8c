"""Who spins in the challenge frame -- the loop's own stack, pushed out of a stuck realm.

    python script/frame_loop.py --seconds 90 --at 5000
    python script/frame_loop.py --seconds 90 --at 5000 --trace   # the condition the flood shows

The trace says the frame reads the global ``Reflect`` at ~8.6k/s and its realm never goes
idle, but a trace callback carries no source location, so it cannot name the reader.  This
installs one bootstrap into every realm the engine creates -- the door is ``pyv8.Context.exec_js``,
which every page script goes through -- wraps ``Reflect``'s own getter, and at the Nth read
captures ``Error().stack`` before **putting the original descriptor back**.

Two constraints shape the reporting, both learned the hard way:

* a realm busy in a JS loop cannot answer ``evaluate`` (60 s, then ``RealmBusyError``), so the
  finding has to be *pushed* -- the bootstrap plants a Python callback in the realm
  (``context.glob``) and calls it from inside the loop, which lands on that realm's own thread;
* workers have no ``window``, and the first ``exec_js`` in a realm can arrive before the global
  is a window at all, so the bootstrap works off ``globalThis`` and a realm is only marked as
  done once it accepted the hook.

Caveat, since it decides how far the finding reaches: wrapping a descriptor is visible to a
page that inspects it, and this challenge does inspect.  The report is evidence of *what the
loop is*, not proof that an unmodified run loops identically.
"""
import argparse
import json
import os
import pathlib
import sys
import threading
import time

URL = "https://steamdb.info/"

#: ``AT`` is replaced by the read count to capture at: early enough to catch a loop that has
#: just started, late enough that a handful of one-off reads does not trigger it.
BOOTSTRAP = r"""
(function () {
  var root = typeof globalThis === 'object' && globalThis ? globalThis : this;
  if (!root || root.__probe) { return 'already'; }
  var probe = root.__probe = { reads: 0, captured: null, error: null, start: Date.now() };
  function send(payload) {
    try { if (typeof __probe_report === 'function') { __probe_report(JSON.stringify(payload)); } }
    catch (error) {}
  }
  var descriptor = Object.getOwnPropertyDescriptor(root, 'Reflect');
  if (!descriptor || typeof descriptor.get !== 'function') {
    probe.error = 'Reflect is not accessor-backed: ' + Object.getOwnPropertyNames(descriptor || {});
    send({ kind: 'no-hook', realm: String(root), error: probe.error });
    return 'no-hook';
  }
  var original = descriptor.get;
  Object.defineProperty(root, 'Reflect', {
    configurable: true,
    get: function () {
      probe.reads += 1;
      if (probe.captured === null && probe.reads === AT) {
        probe.captured = String((new Error()).stack || '(no stack)').slice(0, 1600);
        Object.defineProperty(root, 'Reflect', descriptor);
        send({ kind: 'captured', at: probe.reads, ms: Date.now() - probe.start,
               url: String(root.location ? root.location.href : '?').slice(0, 90),
               stack: probe.captured });
      }
      return original.call(this);
    }
  });
  return 'hooked';
})()
"""

REPORTS = []


def install(at):
    """Plant the callback, then run the bootstrap in every realm before that realm's script."""
    import pyv8

    original = pyv8.Context.exec_js
    done = set()

    def told(text):
        try:
            payload = json.loads(str(text))
        except BaseException:  # noqa: BLE001
            payload = {"kind": "unreadable", "text": str(text)[:400]}
        REPORTS.append(payload)
        print(f"=== report: {json.dumps(payload, ensure_ascii=False)[:1900]}", flush=True)

    def wrapped(self, source, *args, **kwargs):
        key = id(self)
        if key not in done:
            try:
                self.glob["__probe_report"] = told
            except BaseException as error:  # noqa: BLE001
                print(f"! no channel in this realm: {type(error).__name__}: {str(error)[:90]}")
            try:
                original(self, BOOTSTRAP.replace("AT", str(at)), filename="probe-bootstrap.js")
                done.add(key)
            except BaseException as error:  # noqa: BLE001 - retried on the next script
                print(f"! bootstrap deferred: {type(error).__name__}: {str(error)[:90]}")
        return original(self, source, *args, **kwargs)

    pyv8.Context.exec_js = wrapped


def pump(page, seconds, step=0.2):
    deadline = time.time() + seconds
    while True:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001
            pass
        if time.time() >= deadline:
            return
        time.sleep(step)


def browse(at, seconds, trace, log):
    from loguru import logger
    from pyv8c import Browser

    logger.remove()
    logger.add(log, level="DEBUG" if trace else "WARNING", mode="w")
    install(at)

    started = time.time()
    browser = Browser(stub_logging="off", trace=trace, traceable=trace, threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        try:
            page.goto(URL)
        except BaseException as error:  # noqa: BLE001
            print(f"goto: {type(error).__name__}: {str(error)[:100]}")
        pump(page, seconds)
        print(f"=== after {time.time() - started:.0f}s: {len(REPORTS)} report(s)")
        sys.stdout.flush()
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(5.0)
        #: A realm wedged in its own loop is left to the OS rather than waited on.
        os._exit(0 if REPORTS else 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--at", type=int, default=5000, help="capture the stack at this read")
    parser.add_argument("--trace", action="store_true", help="run with the engine trace on")
    parser.add_argument("--log", type=pathlib.Path,
                        default=pathlib.Path(os.environ.get("TEMP", ".")) / "frame_loop.log")
    arguments = parser.parse_args(argv)
    browse(arguments.at, arguments.seconds, arguments.trace, arguments.log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
