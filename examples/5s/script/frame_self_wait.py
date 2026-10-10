"""The wedge behind ``RealmBusyError``, reproduced without the challenge.

The 2026-10-06 run against steamdb.info answered it once, in words:

    realm executor did not complete 'set_content' within 60.0s (already running)

so a frame's document commit started and never returned.  A frame commits while its parent's
realm is running, and this page makes that concrete: the child document carries a script that
calls back into the parent, and the parent's turn cannot end until the child's commit does.

The cadence matters: pumping the queue every 0.05s wedged 3/3 in the earlier measurement while
0.3s passed 1/1 -- whoever gets to the thread first decides.  This probe pumps fast and dumps
*all* thread stacks when the guard fires, because the realm that never answered is another
thread and its own stack is the one that says what it is holding.

    python script/frame_self_wait.py
"""
import faulthandler
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills"
                       / "pyv8c-challenge-parity" / "scripts"))

URL = "https://probe.test/frame-self-wait.html"

#: ``</script>`` through a string literal would close the page's own script tag early.
_INNER = ("<!doctype html><body>inner<scr" + "ipt>"
          "parent.postMessage('frame-ran', '*')</scr" + "ipt></body>")


def watch():
    """Report every executor timeout with the stack that asked and the stacks that held."""
    from pyv8c.foundation.host_errors import RealmBusyError
    from pyv8c.foundation.executor import RealmExecutor

    original = RealmExecutor.call

    def wrapped(self, callback, *args, **kwargs):
        try:
            return original(self, callback, *args, **kwargs)
        except RealmBusyError as error:
            print(f"! realm busy: {error}")
            lines = traceback.format_exc().splitlines()
            for entry in lines[:12] + ["      ..."] + lines[-6:]:
                print(f"      {entry.strip()[:150]}")
            print("! thread dump:")
            faulthandler.dump_traceback()
            raise

    RealmExecutor.call = wrapped


def main():
    import json

    from loguru import logger

    from pyv8c import Browser

    logger.remove()
    watch()

    def load(url, request):
        if not str(url).startswith(("http://", "https://")):
            return None
        return {"body": PAGE, "headers": {"content-type": "text/html"}}

    PAGE = PAGE_HTML.replace("__INNER__", json.dumps(_INNER).replace("</", "<\\/"))
    browser = Browser(stub_logging="off", threaded_loaders=True, real_network=False,
                      resource_loader=load)
    try:
        page = browser.new_context().new_page()
        done = threading.Event()

        def navigate():
            try:
                page.goto(URL)
            except BaseException as error:  # noqa: BLE001
                print(f"  goto raised {type(error).__name__}: {str(error)[:90]}")
            finally:
                done.set()

        threading.Thread(target=navigate, daemon=True).start()
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and not done.is_set():
            try:
                page.run_until_idle(timeout=0)
            except BaseException:  # noqa: BLE001
                pass
            time.sleep(0.05)
        print(f"  finished within 8s: {done.is_set()}")
        try:
            print(f"  out: {page.evaluate('JSON.stringify(window.__out || {})')}")
        except BaseException as error:  # noqa: BLE001
            print(f"  out unreadable: {type(error).__name__}: {str(error)[:80]}")
    finally:
        import os
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(5.0)
        sys.stdout.flush()
        os._exit(0)


PAGE_HTML = """<!doctype html><body><script>
var out = {}; window.__out = out;
window.addEventListener('message', function (event) {
    out.ran = String(event.data);
});
var frame = document.createElement('iframe');
frame.srcdoc = __INNER__;
document.body.appendChild(frame);
</script></body>"""


if __name__ == "__main__":
    raise SystemExit(main())
