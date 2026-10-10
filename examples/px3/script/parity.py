"""Minimal two-engine comparisons, one case at a time.

    python script/parity.py                 # every case
    python script/parity.py rect ua         # only these
    python script/parity.py --side here     # only pyv8c, to see its own answer

Each case is a self-contained page plus one probe, so a difference it reports is
reproducible without the challenge page. Cases that came out of real findings:
``replaced`` (default object size), ``ua`` (user-agent sheet), ``rect`` (DOMRect),
``nodes`` (element identity across creation paths), ``css`` (linked sheets),
``frames`` (animation-frame pacing).
"""
import sys

from common import URL, chrome_page, compare, here, launch, offline, routes, snapshot, there

#: What an element reads as: box, computed display, border, and own properties.
ELEMENT_VIEW = """
var view = function (node) {
  if (!node) { return 'missing'; }
  var out = {};
  try {
    var rect = node.getBoundingClientRect() || { width: 0, height: 0 };
    out.box = Math.round(rect.width) + 'x' + Math.round(rect.height);
  } catch (e) { out.box = 'threw'; }
  try {
    var style = getComputedStyle(node);
    out.display = String(style.display);
    out.border = [style.borderTopWidth, style.borderTopStyle, style.borderLeftWidth].join(' ');
    out.margins = [style.marginTop, style.marginRight, style.marginBottom, style.marginLeft].join(' ');
    out.padding = style.paddingTop + ' ' + style.paddingLeft;
  } catch (e) { out.display = 'threw'; }
  try { out.own = Object.getOwnPropertyNames(node).length; } catch (e) { out.own = 'threw'; }
  return out;
};
"""

REPLACED_PAGE = """<!doctype html><html><body>
<canvas id="canvas"></canvas><img id="img"><iframe id="iframe"></iframe>
<video id="video"></video><object id="object"></object><embed id="embed">
<svg id="svg"></svg><progress id="progress"></progress><textarea id="textarea"></textarea>
<input id="input"><select id="select"><option>a</option></select><button id="button">b</button>
</body></html>"""

UA_PAGE = """<!doctype html><html><body>
<div id="marker"></div><p id="p">text</p><h1 id="h1">t</h1><h2 id="h2">t</h2>
<ul id="ul"><li id="li">t</li></ul><ol id="ol"><li>t</li></ol>
<dl id="dl"><dt id="dt">t</dt><dd id="dd">t</dd></dl>
<blockquote id="blockquote">t</blockquote><figure id="figure">t</figure>
<pre id="pre">t</pre><form id="form"></form><fieldset id="fieldset"></fieldset>
<hr id="hr"><table id="table"><tr><td id="td">t</td></tr></table>
<header id="header">t</header>
</body></html>"""

RECT_PAGE = """<!doctype html><html><body>
<div id="box" style="width: 40px; height: 20px"></div>
<div id="hidden" style="display: none"></div><iframe id="frame"></iframe><svg id="svg"></svg>
</body></html>"""

VIEWPORT_PAGE = """<!doctype html><html><body><div id="d"></div></body></html>"""

NODES_PAGE = """<!doctype html><html><body>
<template id="tpl"><iframe id="fromTpl" token="t1"></iframe></template>
<iframe id="child"></iframe><div id="host"></div>
<script>
window.__nodes = {};
var host = document.getElementById('host');
window.__steps = {};
var step = function (label, thunk) {
  try { window.__nodes[label] = thunk(); window.__steps[label] = 'ok'; }
  catch (e) { window.__steps[label] = 'threw: ' + String(e).slice(0, 50); }
};
step('parsed', function () { return document.getElementById('child'); });
step('created', function () {
  var node = document.createElement('iframe'); node.setAttribute('token', 'c');
  host.appendChild(node); return node; });
step('template', function () {
  var node = document.getElementById('tpl').content.firstElementChild;
  host.appendChild(node); return node; });
step('cloned', function () {
  var node = document.getElementById('tpl').content.cloneNode(true).firstElementChild;
  host.appendChild(node); return node; });
step('imported', function () {
  var node = document.importNode(document.getElementById('tpl').content, true)
    .firstElementChild;
  host.appendChild(node); return node; });
step('inert', function () {
  var inert = document.implementation.createHTMLDocument('');
  var node = inert.createElement('iframe'); node.setAttribute('token', 'i');
  host.appendChild(node); return node; });
step('moved', function () {
  var node = document.createElement('iframe'); node.setAttribute('token', 'm');
  host.appendChild(node); host.removeChild(node); document.body.appendChild(node);
  return node; });
step('ranged', function () {
  var fragment = document.createRange()
    .createContextualFragment('<iframe id="range" token="r"></iframe>');
  var node = fragment.firstElementChild; host.appendChild(node); return node; });
step('crossRealm', function () {
  var child = document.getElementById('child');
  var done = null;
  try {
    done = child.contentWindow.eval(
      "window.__handed = parent.document.getElementById('host').firstElementChild;" +
      "[typeof window.__handed.tagName, window.__handed.attributes ? " +
      "window.__handed.attributes.length : 'no-map'].join('|')");
  } catch (e) { done = 'threw: ' + String(e).slice(0, 40); }
  window.__crossRealm = done;
  return document.getElementById('host').firstElementChild; });
window.__range = (function () {
  try { return String(typeof document.createRange()); } catch (e) { return 'threw'; }
})();
window.__rangeRect = (function () {
  try { return String(typeof document.createRange().getBoundingClientRect()); }
  catch (e) { return 'threw: ' + String(e).slice(0, 30); }
})();
</script>
</body></html>"""

CSS_CASE = """body { margin: 0 } .px-loading-area { width: 310px; height: 100px }
#plain { color: red }
@media (min-width: 100px) { #inMedia { color: blue } }
@supports (display: flex) { #inSupports { height: 7px } }"""

CSS_PAGE = """<!doctype html><html><head>
<link rel='stylesheet' href='/case.css'>
<style>#inline { color: green } @media (min-width: 1px) { #inlineMedia { width: 3px } }</style>
</head><body>
<div id='item' class='px-loading-area'>x</div><div id='plain'>y</div>
<div id='inMedia'>m</div><div id='inSupports'>s</div><div id='inline'>i</div>
<div id='inlineMedia'>n</div></body></html>"""

FRAMES_PAGE = """<!doctype html><html><body><div id="d"></div><script>
window.__frames = { sameTurn: null, count: 0, gaps: [] };
var seen = [];
requestAnimationFrame(function (t) { seen.push(t); });
requestAnimationFrame(function (t) {
  seen.push(t);
  window.__frames.sameTurn = seen[0] === seen[1];
  window.__frames.first = seen[0];
  window.__frames.second = seen[1];
});
var last = null;
var tick = function (t) {
  window.__frames.count++;
  if (last !== null) { window.__frames.gaps.push(Math.round((t - last) * 100) / 100); }
  last = t;
  if (window.__frames.count < 400) { requestAnimationFrame(tick); }
};
requestAnimationFrame(tick);
</script></body></html>"""


def element_case(ids):
    """One expression: report every listed element through the element view."""
    return "(function () {" + ELEMENT_VIEW + """
  var out = {};
  var ids = %s;
  for (var i = 0; i < ids.length; i++) { out[ids[i]] = view(document.getElementById(ids[i])); }
  return out;
})()
""" % ids


CASES = {
    "replaced": {
        "url": "https://probe.test/replaced.html",
        "page": REPLACED_PAGE,
        "probe": element_case("['canvas','img','iframe','video','object','embed',"
                                    "'svg','progress','textarea','input','select','button']"),
    },
    "ua": {
        "url": "https://probe.test/ua.html",
        "page": UA_PAGE,
        "probe": element_case("['marker','p','h1','h2','ul','ol','dl','blockquote',"
                                    "'figure','pre','form','fieldset','hr','table','header']"),
    },
    "rect": {
        "url": "https://probe.test/rect.html",
        "page": RECT_PAGE,
        "probe": """(function () {
  var out = {};
  var ids = ['box', 'hidden', 'frame', 'svg'];
  for (var i = 0; i < ids.length; i++) {
    var node = document.getElementById(ids[i]);
    try {
      var rect = node.getBoundingClientRect();
      out[ids[i]] = {
        type: Object.prototype.toString.call(rect),
        ctor: String(rect.constructor.name),
        isDOMRect: typeof DOMRect === 'undefined' ? 'no-DOMRect' : (rect instanceof DOMRect),
        keys: Object.keys(rect).length,
        json: typeof rect.toJSON === 'function' ? JSON.stringify(rect.toJSON()) : 'no-toJSON',
        box: [rect.x, rect.y, rect.width, rect.height].join(',')
      };
    } catch (e) { out[ids[i]] = 'threw: ' + String(e).slice(0, 40); }
  }
  out.domRectProto = typeof DOMRect === 'undefined' ? 'missing'
    : Object.getOwnPropertyNames(DOMRect.prototype).join(',');
  return out;
})()""",
    },
    "viewport": {
        "url": "https://probe.test/viewport.html",
        "page": VIEWPORT_PAGE,
        "probe": """(function () {
  var inner = window.innerWidth;
  return {
    innerWidth: inner, innerHeight: window.innerHeight,
    htmlClient: document.documentElement.clientWidth,
    bodyOffset: document.body.offsetWidth,
    bodyX: Math.round(document.body.getBoundingClientRect().x),
    divOffset: document.getElementById('d').offsetWidth,
    screenWidth: screen.width,
    scrollbar: inner - document.documentElement.clientWidth,
    divFillsBody: document.getElementById('d').offsetWidth === document.body.clientWidth
  };
})()""",
    },
    "dom_api": {
        "url": "https://probe.test/api.html",
        "page": VIEWPORT_PAGE,
        "probe": """(function () {
  var names = ['adoptNode', 'importNode', 'createRange', 'createContextualFragment',
               'createTreeWalker', 'createNodeIterator', 'elementFromPoint', 'elementsFromPoint',
               'insertAdjacentHTML', 'attachShadow', 'setHTMLUnsafe', 'getHTML', 'moveBefore',
               'replaceChildren', 'requestPointerLock', 'setPointerCapture', 'animate',
               'checkVisibility', 'computedStyleMap', 'createDocumentFragment'];
  var kinds = { document: document, element: document.getElementById('d'),
                implementation: document.implementation,
                text: document.createTextNode('t') };
  var out = {};
  for (var i = 0; i < names.length; i++) {
    var where = [];
    for (var kind in kinds) {
      var value = null;
      try { value = kinds[kind][names[i]]; } catch (e) { value = 'threw'; }
      if (typeof value === 'function') { where.push(kind); }
    }
    out[names[i]] = where.length ? where.join('+') : 'missing';
  }
  return out;
})()""",
    },
    "nodes": {
        "url": "https://probe.test/nodes.html",
        "page": NODES_PAGE,
        "probe": """(function () {
  var out = { steps: window.__steps, crossRealm: window.__crossRealm,
              range: window.__range, rangeRect: window.__rangeRect };
  var read = function (name) {
    var node = window.__nodes[name];
    if (!node) { return 'missing'; }
    var text = [];
    try { text.push(String(node.tagName)); } catch (e) { text.push('threw'); }
    try { text.push(node.attributes ? node.attributes.length : 'no-map'); }
    catch (e) { text.push('threw'); }
    try { text.push('own=' + Object.getOwnPropertyNames(node).length); } catch (e) { }
    try { text.push(node.ownerDocument === document ? 'sameDoc' : 'otherDoc'); } catch (e) { }
    try { text.push(typeof node.contentDocument); } catch (e) { }
    return text.join('|');
  };
  for (var name in window.__nodes) { out[name] = read(name); }
  return out;
})()""",
    },
    "css": {
        "url": "https://probe.test/css.html",
        "page": CSS_PAGE,
        "route": {"/case.css": (CSS_CASE, "text/css"), "/css.html": (CSS_PAGE, "text/html")},
        "probe": """(function () {
  var out = { sheets: [], linked: 'missing' };
  for (var i = 0; i < document.styleSheets.length; i++) {
    var sheet = document.styleSheets[i];
    var entry = { href: String(sheet.href || 'inline').slice(-14) };
    try { entry.rules = sheet.cssRules ? sheet.cssRules.length : '?'; }
    catch (e) { entry.rules = 'blocked'; }
    var px = null;
    try {
      if (sheet.cssRules) {
        for (var j = 0; j < sheet.cssRules.length; j++) {
          var text = String(sheet.cssRules[j].cssText || '');
          if (text.indexOf('px-loading-area') >= 0) { px = text.slice(0, 60); break; }
        }
      }
    } catch (e) { }
    entry.sample = px;
    out.sheets.push(entry);
  }
  var item = document.getElementById('item');
  out.linked = (function () {
    var rect = item.getBoundingClientRect();
    return Math.round(rect.width) + 'x' + Math.round(rect.height);
  })();
  out.plain = getComputedStyle(document.getElementById('plain')).color;
  out.inMedia = getComputedStyle(document.getElementById('inMedia')).color;
  out.inSupports = getComputedStyle(document.getElementById('inSupports')).height;
  out.inline = getComputedStyle(document.getElementById('inline')).color;
  out.inlineMedia = getComputedStyle(document.getElementById('inlineMedia')).width;
  return out;
})()""",
    },
    "frames": {
        "url": "https://probe.test/frames.html",
        "page": FRAMES_PAGE,
        "settle": 3.0,
        "probe": """(function () {
  var frames = window.__frames;
  var gaps = frames.gaps;
  return {
    sameTurnSharesStamp: frames.sameTurn,
    first: Math.round(frames.first * 10) / 10,
    frames: frames.count,
    minGap: gaps.length ? Math.min.apply(null, gaps) : 0,
    maxGap: gaps.length ? Math.max.apply(null, gaps) : 0
  };
})()""",
    },
}


def run(name, case, side):
    """One case, one engine: the loader/route, the page, and the probe's answer."""
    url = case["url"]
    settle = case.get("settle", 0.6)
    if side == "here":
        with here(url, seconds=settle, loader=offline(case["page"])) as page:
            return snapshot(page, case["probe"])
    route = routes(case["route"]) if case.get("route") else None
    if route is None:
        route = routes({url.split("probe.test")[-1]: (case["page"], "text/html")})
    with there(url, seconds=settle, route=route) as page:
        return snapshot(page, case["probe"])


def main(argv):
    wanted, side, index = [], None, 0
    while index < len(argv):
        item = argv[index]
        if item == "--side":
            side = argv[index + 1]
            index += 2
            continue
        if not item.startswith("-"):
            wanted.append(item)
        index += 1
    names = wanted or list(CASES)
    failures = 0
    for name in names:
        case = CASES.get(name)
        if case is None:
            print(f"=== {name}: unknown case")
            continue
        print(f"=== {name}")
        if side:
            answer = run(name, case, side)
            for key, value in answer.items():
                print(f"    {key:14} {value}")
            continue
        mine = run(name, case, "here")
        if mine.get("error"):
            print("    pyv8c failed:", mine["error"])
            failures += 1
            continue
        theirs = run(name, case, "chrome")
        if theirs.get("error"):
            print("    chrome failed:", theirs["error"])
            continue
        if compare(mine, theirs):
            failures += 1
    print(f"== cases with differences: {failures}/{len(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
