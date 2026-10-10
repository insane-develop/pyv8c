"""What the challenge page is doing, in either engine.

    python script/challenge.py timeline both 30   # the widget's state, second by second
    python script/challenge.py tree               # the widget's subtree
    python script/challenge.py frames 40          # Chrome's frame list and their text
    python script/challenge.py sheets both        # every stylesheet and its rule count
    python script/challenge.py fresh chrome 20    # ask for a challenge with a fresh uuid

The button lives inside the challenge's own document, which the parent cannot read
(see the hidden element wrapper both engines report), so ``frames`` and ``tree`` are
read from the engine side instead of the parent document.
"""
import sys
import time
import uuid

from common import URL, compare, here, launch, chrome_page, snapshot, there

#: The widget's box, its child count, and what the frame reads back as.
STATE = r"""
(function () {
  var size = function (node) {
    if (!node) { return 'none'; }
    try {
      var rect = node.getBoundingClientRect();
      return rect ? Math.round(rect.width) + 'x' + Math.round(rect.height) : 'norect';
    } catch (e) { return 'error'; }
  };
  var node = document.querySelector('#px-captcha');
  if (!node) { return { state: 'absent' }; }
  var out = { box: size(node), children: node.children.length };
  var frame = document.querySelector('#px-captcha iframe');
  if (frame) {
    var attrs = [];
    try {
      var map = frame.attributes || {};
      for (var i = 0; i < (map.length || 0); i++) {
        attrs.push(map[i].name + '=' + String(map[i].value).slice(0, 18));
      }
    } catch (e) { attrs = ['?']; }
    var style = null;
    try { style = getComputedStyle(frame); } catch (e) { }
    out.frame = { attrs: attrs, display: String((style || {}).display || ''),
                  box: size(frame) };
  }
  return out;
})()
"""

TREE = r"""
(function () {
  var root = document.querySelector('#px-captcha');
  if (!root) { return { rows: ['absent'] }; }
  var rows = [];
  var walk = function (node, depth) {
    if (depth > 3 || rows.length > 18) { return; }
    var rect = null, style = null;
    try { rect = node.getBoundingClientRect(); } catch (e) { rect = null; }
    try { style = getComputedStyle(node); } catch (e) { style = null; }
    rect = rect || { width: 0, height: 0 };
    style = style || {};
    rows.push('  '.repeat(depth) + String(node.tagName) + ' | ' +
      String(node.className || '').slice(0, 24) + ' | ' +
      Math.round(rect.width) + 'x' + Math.round(rect.height) + ' | d=' +
      String(style.display || '').slice(0, 5) + ' | h=' + String(style.height || '').slice(0, 8) +
      ' | ' + String((node.getAttribute && node.getAttribute('style')) || '').slice(0, 44));
    var kids = node.children || [];
    for (var i = 0; i < kids.length; i++) { walk(kids[i], depth + 1); }
  };
  walk(root, 0);
  return { rows: rows };
})()
"""

SHEETS = r"""
(function () {
  var out = { adopted: 'missing', adoptedCount: 0, sheets: [] };
  try {
    if ('adoptedStyleSheets' in document) {
      out.adopted = 'present';
      out.adoptedCount = document.adoptedStyleSheets.length;
    }
  } catch (e) { out.adopted = 'threw'; }
  for (var i = 0; i < document.styleSheets.length; i++) {
    var sheet = document.styleSheets[i];
    var entry = { href: String(sheet.href || 'inline').slice(-24) };
    try { entry.rules = sheet.cssRules ? sheet.cssRules.length : '?'; }
    catch (e) { entry.rules = 'blocked'; }
    var px = null;
    try {
      if (sheet.cssRules) {
        for (var j = 0; j < sheet.cssRules.length; j++) {
          var text = String(sheet.cssRules[j].cssText || '');
          if (text.indexOf('px-captcha') >= 0) { px = text.slice(0, 80); break; }
        }
      }
    } catch (e) { }
    entry.pxCaptcha = px;
    out.sheets.push(entry);
  }
  var node = document.querySelector('#px-captcha');
  out.box = node ? (function () {
    var rect = node.getBoundingClientRect();
    var style = getComputedStyle(node);
    return Math.round(rect.width) + 'x' + Math.round(rect.height) + ' h=' + style.height;
  })() : 'absent';
  return out;
})()
"""

STILL = r"""
(function () {
  var node = document.querySelector('#px-captcha');
  if (!node) { return 'absent'; }
  var rect = node.getBoundingClientRect();
  return Math.round(rect.width) + 'x' + Math.round(rect.height) + ' children=' +
    node.children.length;
})()
"""


def timeline(side, seconds):
    """The widget's state once a second, so a lifecycle shows up as a sequence."""
    if side == "here":
        with here(URL, seconds=2) as page:
            rows = []
            for _ in range(int(seconds)):
                rows.append(snapshot(page, STATE))
                time.sleep(1.0)
            return rows
    rows = []
    with there(URL, seconds=0.5) as page:
        for _ in range(int(seconds)):
            rows.append(snapshot(page, STATE))
            page.wait_for_timeout(1000)
        return rows


def frames(seconds):
    """Chrome's own frame list: what the challenge frame holds and says."""
    answer = []
    with there(URL, seconds=seconds) as page:
        for index, frame in enumerate(page.frames):
            try:
                text = frame.evaluate("String(document.body ? document.body.innerText : '')")
            except Exception as error:  # noqa: BLE001
                text = f"<{type(error).__name__}>"
            try:
                heading = frame.evaluate(
                    "(function () { var out = [];"
                    " document.querySelectorAll('h1,h2,button,section').forEach(function (node) {"
                    "   var own = String(node.textContent || '').replace(/\\s+/g, ' ').trim();"
                    "   if (own && own.length < 60) { out.push(node.tagName + ':' + own); } });"
                    " return out.slice(0, 5); })()")
            except Exception as error:  # noqa: BLE001
                heading = [f"<{type(error).__name__}>"]
            answer.append({"index": index, "url": frame.url[:70],
                           "text": " ".join(text.split())[:180], "shapes": heading})
    return answer


def fresh(side, seconds):
    """The same page with a uuid that was never used before."""
    head, _, tail = URL.partition("uuid=")
    value = str(uuid.uuid4())
    rest = tail.split("&", 1)
    fresh_url = head + "uuid=" + value + ("&" + rest[1] if len(rest) > 1 else "")
    print("uuid:", value)
    if side == "here":
        with here(fresh_url, seconds=2) as page:
            rows = []
            for _ in range(int(seconds)):
                rows.append(snapshot(page, STILL))
                time.sleep(1.0)
            return rows
    rows = []
    with there(fresh_url, seconds=0.5) as page:
        for _ in range(int(seconds)):
            rows.append(snapshot(page, STILL))
            page.wait_for_timeout(1000)
    return rows


def main(argv):
    mode = argv[0] if argv else "timeline"
    side = "both"
    seconds = 25.0
    rest = []
    for item in argv[1:]:
        if item in ("here", "chrome", "both"):
            side = item
        else:
            rest.append(item)
    if rest:
        seconds = float(rest[0])

    if mode == "frames":
        for entry in frames(seconds):
            print("   ", entry)
        return 0
    if mode == "fresh":
        sides = ("here", "chrome") if side == "both" else (side,)
        for name in sides:
            print(f"=== {name}")
            for row in fresh(name, seconds):
                print("   ", row)
        return 0

    probe = {"tree": TREE, "sheets": SHEETS}.get(mode)
    sides = ("here", "chrome") if side == "both" else (side,)
    if probe is None:
        for name in sides:
            print(f"=== {name}")
            for row in timeline(name, seconds):
                print("   ", row)
        return 0

    states = {}
    for name in sides:
        print(f"=== {name}")
        states[name] = snapshot_of(name, probe)
        for row in states[name].get("rows", []):
            print("   ", row)
    if len(states) == 2:
        compare(states["here"], states["chrome"])
    return 0


def snapshot_of(side, probe):
    if side == "here":
        with here(URL, seconds=12) as page:
            return snapshot(page, probe)
    with there(URL, seconds=12) as page:
        return snapshot(page, probe)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
