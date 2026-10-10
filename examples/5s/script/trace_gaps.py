"""The trace's sharpest differences, as one local two-sided page (no third-party traffic).

    python script/trace_gaps.py

From the 5s trace (2026-10-10), the reads/calls that answered where Chrome answers something:

    ImageData.pixelFormat                       -> undefined   (4)
    OffscreenCanvasRenderingContext2D.getContextAttributes() -> undefined (7)
    WebGL2RenderingContext.getInternalformatParameter(...)   -> undefined (34)
    TextDecoder.decode([object ArrayBuffer])    -> error: TypeError (2)

Each is asked here the way the challenge asks it, on both sides, in one page.
"""
import json
import sys

sys.path.insert(0, r"D:\src\pyv8env\pyv8c\skills\pyv8c-challenge-parity\scripts")

from harness import _ask_page  # noqa: E402

PAGE = """<!doctype html><html><body><script>
window.__out = (function () {
  var out = {};
  function ask(label, thunk) {
    try { out[label] = String(thunk()); } catch (error) { out[label] = 'threw ' + error.name; }
  }
  try {
  ask('readyState', function () { return document.readyState + '/' + (1 + 1); });
  ask('decode uint8', function () {
    return new TextDecoder('utf-8').decode(new Uint8Array([0xE2, 0x82, 0xAC]));
  });
  ask('decode ArrayBuffer', function () {
    return new TextDecoder('utf-8').decode(new Uint8Array([65, 66, 67]).buffer);
  });
  ask('decode DataView', function () {
    return new TextDecoder('utf-8').decode(new DataView(new ArrayBuffer(3)));
  });
  ask('detached buffer', function () {
    var buffer = new ArrayBuffer(4);
    var view = new Uint8Array(buffer);
    if (typeof structuredClone === 'function') { structuredClone(buffer, {transfer: [buffer]}); }
    return view.byteLength + '/' + buffer.byteLength;
  });
  ask('offcanvas attrs', function () {
    var context = new OffscreenCanvas(4, 4).getContext('2d');
    return JSON.stringify(context.getContextAttributes());
  });
  ask('canvas attrs', function () {
    var context = document.createElement('canvas').getContext('2d');
    return JSON.stringify(context.getContextAttributes());
  });
  ask('canvas attrs keys', function () {
    var context = document.createElement('canvas').getContext('2d');
    return Object.keys(context.getContextAttributes() || {}).sort().join(',');
  });
  ask('webgl2 internalformat', function () {
    var gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) { return 'no context'; }
    var value = gl.getInternalformatParameter(gl.RENDERBUFFER, gl.RGBA8, gl.SAMPLES);
    return (typeof value) + ':' + (value && value.length !== undefined ? value.length : value);
  });
  ask('webgl2 float attrs', function () {
    var gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) { return 'no context'; }
    var value = gl.getInternalformatParameter(gl.RENDERBUFFER, gl.RGBA32F, gl.SAMPLES);
    return (typeof value) + ':' + (value && value.length !== undefined ? value.length : value);
  });
  ask('imagedata pixelFormat', function () {
    var image = new ImageData(2, 2);
    return ('pixelFormat' in image) + ':' + typeof image.pixelFormat + ':' + String(image.pixelFormat);
  });
  ask('imagedata colorSpace', function () {
    var image = new ImageData(2, 2);
    return image.colorSpace + '/' + image.width + 'x' + image.height;
  });
  } catch (error) { out['!! script'] = 'threw ' + error.name + ': ' + String(error.message); }
  return out;
})()
""" + "</" + "script></body></html>"


def cell(value, width):
    """One answer as text, ASCII only: this console is GBK, and a euro sign would end the run."""
    text = str(value)
    return "".join(char if char.isascii() and char.isprintable() else "." for char in text)[:width]


if __name__ == "__main__":
    mine, theirs = _ask_page("https://probe.test/trace_gaps.html", PAGE, "window.__out", 2.5)
    left_side = mine if isinstance(mine, dict) else {"<answer>": mine}
    right_side = theirs if isinstance(theirs, dict) else {"<answer>": theirs}
    print(f"   {'question':22} {'pyv8c':46} chrome")
    for key in sorted(set(left_side) | set(right_side)):
        left = cell(left_side.get(key), 46)
        right = cell(right_side.get(key), 60)
        mark = "  " if left == right else "!="
        print(f"{mark} {key:22} {left:46} {right}")
    if not isinstance(mine, dict) or not isinstance(theirs, dict):
        #: Neither side answered an object: the page itself is the suspect, so keep the exact
        #: bytes both engines were handed (a syntax error looks like this on both sides).
        path = pathlib.Path(os.environ.get("TEMP", ".")) / "trace_gaps_page.html"
        path.write_text(PAGE, encoding="utf-8")
        print(f"-> neither side answered an object; the page served is {path}")

