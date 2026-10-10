"""A frame a running script puts into a shadow root: does the parent's turn survive it?

The trace of the 2026-10-06 run has exactly one structural error at the decisive moment (the
widget had just reported success and was waiting for the site to answer):

    [FRAME] method: ShadowRoot.appendChild(<HTMLIFrameElement>) -> undefined,
            error: <RealmBusyError>

``RealmBusyError`` comes from the executor guard: a call into a realm whose owner thread does not
pick it up in time.  Creating a frame *is* such a call -- the child realm is built from the
parent's turn -- so if the parent is the realm being asked, the two wait for each other.

Two witnesses, so "it never ran" cannot be mistaken for "it ran and answered nothing": the
frame's own ``postMessage``, and the ``onload`` the parent sets.

    python script/shadow_frame.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills"
                       / "pyv8c-challenge-parity" / "scripts"))

from harness import _ask_page  # noqa: E402

URL = "https://probe.test/shadow-frame.html"

#: ``</script>`` through a string literal would close the page's own script tag, so the child
#: document is assembled from pieces the HTML parser cannot end a script with.
_INNER = ("<!doctype html><body>inner<scr" + "ipt>parent.postMessage('ran', '*')</scr"
          + "ipt></body>")

PAGE = """<!doctype html><body><script>
var out = {}; window.__out = out;
window.addEventListener('message', function (event) {
  out.ran = String(event.data);
  window.__done = true;
});
try {
  var host = document.createElement('div');
  document.body.appendChild(host);
  var root = host.attachShadow({mode: 'open'});
  var frame = document.createElement('iframe');
  frame.onload = function () { out.load = true; };
  frame.srcdoc = __INNER__;
  root.appendChild(frame);
  out.attached = !!root.querySelector('iframe');
  out.connected = !!frame.isConnected;
} catch (error) {
  out.threw = error.name + ': ' + String(error.message).slice(0, 80);
  window.__done = true;
}
</script></body>"""


def main():
    page_html = PAGE.replace("__INNER__", json.dumps(_INNER).replace("</", "<\\/"))
    mine, theirs = _ask_page(URL, page_html, "JSON.stringify(window.__out || {})", 2.5,
                             "!!window.__done")
    print(f"  pyv8c : {json.dumps(mine, ensure_ascii=False)}")
    print(f"  chrome: {json.dumps(theirs, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
