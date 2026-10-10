"""Two engine gaps the widget walked into, asked of both browsers side by side.

* ``new TextDecoder('iso-8859-1')`` raised ``DOMException`` in the 2026-10-06 run: the engine
  modelled only utf-8 labels, and a label is not a codec name -- the standard files
  ``iso-8859-1`` under *windows-1252*, so byte 0x80 is the euro sign and not a control code.
* ``document.createEvent('TouchEvent')`` raised ``NotSupportedError``: no touch interfaces
  existed at all, and the widget asks for one to build a synthetic touch.

The probe asks each side for the labels' canonical names, the bytes they decode to, and the
shape of the event ``createEvent`` hands back.

    python script/encoding_and_touch.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills"
                       / "pyv8c-challenge-parity" / "scripts"))

from harness import _ask_page  # noqa: E402

URL = "https://probe.test/encoding-touch.html"

SCRIPT = r"""
(function () {
  var out = {};
  var labels = ['utf-8', 'iso-8859-1', 'latin1', 'us-ascii', 'windows-1252', 'utf-16le',
                'shift_jis', 'gbk'];
  out.encodings = labels.map(function (label) {
    try { return label + '=' + new TextDecoder(label).encoding; }
    catch (error) { return label + '=' + error.name; }
  });
  out.bytes = (function () {
    var data = new Uint8Array([0x80, 0xE9, 0x41]);
    return ['utf-8', 'iso-8859-1'].map(function (label) {
      try {
        return label + '=' + JSON.stringify(new TextDecoder(label).decode(data));
      } catch (error) { return label + '=' + error.name; }
    });
  })();
  out.unknown = (function () {
    try { new TextDecoder('bogus-encoding'); return 'no error'; }
    catch (error) { return error.name; }
  })();
  out.touch = (function () {
    try {
      var event = document.createEvent('TouchEvent');
      var list = document.createEvent('TouchEvent').touches;
      return [Object.prototype.toString.call(event), event.type,
              event.touches ? event.touches.length : 'no touches',
              event.changedTouches ? event.changedTouches.length : 'no changed',
              typeof event.initTouchEvent, event instanceof TouchEvent,
              Object.prototype.toString.call(list)];
    } catch (error) { return error.name + ': ' + String(error.message).slice(0, 60); }
  })();
  out.init = (function () {
    try {
      var event = document.createEvent('TouchEvent');
      event.initTouchEvent('touchstart', true, true);
      return [event.type, event.bubbles, event.cancelable];
    } catch (error) { return error.name + ': ' + String(error.message).slice(0, 60); }
  })();
  return JSON.stringify(out);
})()
"""


def main():
    mine, theirs = _ask_page(URL, "<!doctype html><body>probe", SCRIPT, 1.5, None)
    #: ASCII escapes only: a decoded replacement character cannot be printed to a GBK console.
    print(f"  pyv8c : {json.dumps(mine)}")
    print(f"  chrome: {json.dumps(theirs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
