"""An inline frame inside a secure page: is the *frame's* context secure?

The widget frame Cloudflare builds in this case is a document with **no scheme of its own** --
``about:blank``, or an inline ``srcdoc`` inside a ``sandbox`` attribute -- and the page then
reads ``navigator.userActivation``, ``navigator.clipboard`` and 29 relatives off it.  Chrome
answers those from the *inherited* context, pyv8c answered ``undefined`` for 31 of them in the
2026-10-06 run.

Two frames, two witnesses:

* ``same`` -- an inline ``about:blank`` frame the parent reads directly (that is the shape the
  widget uses, and the reason the page can read it at all);
* ``sandboxed`` -- ``sandbox="allow-scripts"``, which has no same-origin access from the parent,
  so the frame reports itself by ``postMessage``.  Without this second channel, "the frame never
  ran" and "the frame ran and answered undefined" would look the same.

    python script/secure_frame.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills"
                       / "pyv8c-challenge-parity" / "scripts"))

from harness import _ask_page  # noqa: E402

URL = "https://probe.test/secure-frame.html"

MEMBERS = ["userActivation", "clipboard", "credentials", "mediaDevices", "storage"]

#: ``</script>`` inside a string literal would close *this* page's script tag, so the inner
#: document is assembled from pieces the HTML parser cannot end a script with.
_INNER = ("<!doctype html><body><scr" + "ipt>parent.postMessage({"
          " probe: true, secure: window.isSecureContext, url: document.URL,"
          " members: [" + ", ".join(f"typeof navigator.{name}" for name in MEMBERS) + "]"
          "}, '*')</scr" + "ipt></body>")

PAGE = """<!doctype html><body><script>
var out = {}; window.__out = out;
window.addEventListener('message', function (event) {
  if (event.data && event.data.probe) { out.sandboxed = event.data; window.__done = true; }
});
var box = document.createElement('iframe');
box.setAttribute('sandbox', 'allow-scripts');
box.srcdoc = __INNER__;
document.body.appendChild(box);
var same = document.createElement('iframe');
document.body.appendChild(same);
setTimeout(function () {
  try {
    var view = same.contentWindow;
    out.same = {
      secure: view.isSecureContext,
      url: same.contentDocument.URL,
      members: [__MEMBERS__].map(function (name) { return typeof view.navigator[name]; })
    };
  } catch (error) { out.same = { error: String(error) }; }
}, 300);
</script></body>"""


def main():
    page_html = (PAGE
                 .replace("__INNER__", json.dumps(_INNER).replace("</", "<\\/"))
                 .replace("__MEMBERS__", json.dumps(MEMBERS)))
    mine, theirs = _ask_page(URL, page_html, "JSON.stringify(window.__out || {})", 2.5,
                             "!!window.__done")
    print(f"  pyv8c : {json.dumps(mine, ensure_ascii=False)}")
    print(f"  chrome: {json.dumps(theirs, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
