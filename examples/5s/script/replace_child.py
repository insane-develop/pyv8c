"""``replaceChild(undefined, node)``: a raise only counts as a difference if the other side agrees.

The 23:04 trace had ``[FRAME] method: HTMLBodyElement.replaceChild(undefined, <HTMLDivElement>)
-> undefined, error: ...`` and the sweep listed it as an engine raise.  An engine raise is not a
divergence by itself -- an invalid argument raises everywhere -- so this asks the same call of
both browsers, together with the valid call that must keep working.

    python script/replace_child.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills"
                       / "pyv8c-challenge-parity" / "scripts"))

from harness import _ask_page  # noqa: E402

URL = "https://probe.test/replace-child.html"

SCRIPT = r"""
(function () {
  var out = {};
  function attempt(label, fn) {
    try { return label + '=' + fn(); }
    catch (error) { return label + '=' + error.name + ': ' + String(error.message); }
  }
  var first = document.createElement('div');
  var second = document.createElement('div');
  document.body.appendChild(first);
  out.invalid = attempt('undefined-old', function () {
    return document.body.replaceChild(undefined, first) ? 'replaced' : 'null';
  });
  out.missing = attempt('missing-old', function () {
    return document.body.replaceChild(second, document.createElement('p')) ? 'replaced' : 'null';
  });
  out.valid = attempt('valid', function () {
    var node = document.createElement('span');
    document.body.appendChild(node);
    var replaced = document.body.replaceChild(first, node);
    return replaced.tagName + '/' + (node.isConnected ? 'removed' : 'gone');
  });
  out.null_old = attempt('null-old', function () {
    return document.body.replaceChild(null, document.createElement('p')) ? 'replaced' : 'null';
  });
  return JSON.stringify(out);
})()
"""


def main():
    mine, theirs = _ask_page(URL, "<!doctype html><body>probe", SCRIPT, 1.5, None)
    print(f"  pyv8c : {json.dumps(mine)}")
    print(f"  chrome: {json.dumps(theirs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
