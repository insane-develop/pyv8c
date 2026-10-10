"""A library of site-agnostic probes.

Every probe is one JavaScript expression returning JSON-safe data, so the same
text runs in pyv8c and in Chrome and the two answers can be diffed field by
field. Probes are deliberately tolerant: a page may replace its own nodes with
wrappers that answer `undefined` for everything, and that must not throw.

Add a probe whenever a new site shows a difference: it becomes a regression case
that needs no access to that site.
"""

#: Element readings shared by the layout/style probes: box, computed values, own props.
ELEMENT_VIEW = r"""
var __view = function (node) {
  if (!node) { return 'missing'; }
  var out = {};
  try {
    var rect = node.getBoundingClientRect() || { width: 0, height: 0 };
    out.box = Math.round(rect.width) + 'x' + Math.round(rect.height);
  } catch (e) { out.box = 'threw'; }
  try {
    var style = getComputedStyle(node);
    out.display = String(style.display);
    out.size = [style.width, style.height].join(' ');
    out.margin = [style.marginTop, style.marginRight, style.marginBottom,
                  style.marginLeft].join(' ');
    out.padding = [style.paddingTop, style.paddingRight, style.paddingBottom,
                   style.paddingLeft].join(' ');
    out.border = [style.borderTopWidth, style.borderTopStyle, style.borderTopColor].join(' ');
    out.boxSizing = String(style.boxSizing);
    out.position = String(style.position);
  } catch (e) { out.display = 'threw'; }
  try { out.own = Object.getOwnPropertyNames(node).length; } catch (e) { out.own = 'threw'; }
  return out;
};
// The part a diff should show: geometry and how the box is laid out. Computed
// margins/borders are reported once per page instead, because a page that never
// resolves them would otherwise drown every element in the same difference.
var __brief = function (node) {
  var out = __view(node);
  return { box: out.box, display: out.display, size: out.size,
           position: out.position, own: out.own };
};
var __marginSupport = function () {
  try {
    var probe = document.createElement('div');
    probe.style.margin = '5px';
    document.body.appendChild(probe);
    var value = getComputedStyle(probe).marginTop;
    document.body.removeChild(probe);
    return String(value || '(empty)');
  } catch (e) { return 'threw'; }
};
var __tags = function (node) {
  return String(node.tagName) + (node.id ? '#' + node.id : '') +
         (node.className ? '.' + String(node.className).split(/\s+/)[0] : '');
};
// The first few elements in document order, plus the two roots: deterministic, no
// site-specific selectors.
var __pick = function (limit) {
  var out = [document.documentElement, document.body];
  var all = document.querySelectorAll('*');
  for (var i = 0; i < all.length && out.length < (limit + 2); i++) {
    if (all[i] !== document.body && all[i] !== document.documentElement) { out.push(all[i]); }
  }
  return out;
};
"""


def _probe(body):
    """One expression: the shared element view, then *body*, which ends in ``return``."""
    return "(function () {" + ELEMENT_VIEW + body + "\n})()"

UA_PAGE = """<!doctype html><html><body>
<div id="marker"></div><p id="p">text</p><h1 id="h1">t</h1><h2 id="h2">t</h2>
<ul id="ul"><li id="li">t</li></ul><ol id="ol"><li>t</li></ol>
<dl id="dl"><dt id="dt">t</dt><dd id="dd">t</dd></dl>
<blockquote id="blockquote">t</blockquote><figure id="figure">t</figure>
<pre id="pre">t</pre><form id="form"></form><fieldset id="fieldset"></fieldset>
<hr id="hr"><table id="table"><tr><td id="td">t</td></tr></table>
<header id="header">t</header>
</body></html>"""

REPLACED_PAGE = """<!doctype html><html><body>
<canvas id="canvas"></canvas><img id="img"><iframe id="iframe"></iframe>
<video id="video"></video><object id="object"></object><embed id="embed">
<svg id="svg"></svg><progress id="progress"></progress><textarea id="textarea"></textarea>
<input id="input"><select id="select"><option>a</option></select><button id="button">b</button>
</body></html>"""

RECT_PAGE = """<!doctype html><html><body>
<div id="box" style="width: 40px; height: 20px"></div>
<div id="hidden" style="display: none"></div><iframe id="frame"></iframe><svg id="svg"></svg>
</body></html>"""

NODES_PAGE = """<!doctype html><html><body>
<template id="tpl"><div id="fromTpl" token="t1"></div></template>
<div id="host"></div>
<script>
window.__nodes = {}; window.__steps = {};
var host = document.getElementById('host');
var step = function (label, thunk) {
  try { window.__nodes[label] = thunk(); window.__steps[label] = 'ok'; }
  catch (e) { window.__steps[label] = 'threw: ' + String(e).slice(0, 50); }
};
step('parsed', function () { return document.getElementById('host'); });
step('created', function () {
  var node = document.createElement('div'); node.id = 'created';
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
step('adopted', function () {
  var inert = document.implementation.createHTMLDocument('');
  var node = inert.createElement('div'); node.id = 'adopted';
  host.appendChild(node); return node; });
step('moved', function () {
  var node = document.createElement('div'); node.id = 'moved';
  host.appendChild(node); host.removeChild(node); document.body.appendChild(node);
  return node; });
step('ranged', function () {
  var fragment = document.createRange()
    .createContextualFragment('<div id="ranged">x</div>');
  var node = fragment.firstElementChild; host.appendChild(node); return node; });
window.__range = String(typeof document.createRange());
window.__rangeRect = String(typeof document.createRange().getBoundingClientRect());
</script>
</body></html>"""

FRAMES_PAGE = """<!doctype html><html><body><div id="d"></div><script>
window.__frames = { sameTurn: null, count: 0, gaps: [] };
var seen = [];
requestAnimationFrame(function (t) { seen.push(t); });
requestAnimationFrame(function (t) {
  seen.push(t);
  window.__frames.sameTurn = seen[0] === seen[1];
  window.__frames.first = seen[0];
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

#: ``name: {"probe": js, "note": what it answers, "fixture": (page, seconds)}``.
#: A fixture makes the probe runnable without any site; probes without one run on
#: whatever ``--url`` the caller gives.
PROBES = {
    "layout": {
        "note": "boxes and computed style of the page's first elements",
        "probe": _probe("""
  var out = {};
  var nodes = __pick(8);
  for (var i = 0; i < nodes.length; i++) { out[__tags(nodes[i])] = __brief(nodes[i]); }
  out.__computedMargin = __marginSupport();
  out.__counts = { elements: document.querySelectorAll('*').length,
                   bodyChildren: document.body ? document.body.children.length : 0,
                   scroll: [document.documentElement.scrollWidth,
                            document.documentElement.scrollHeight].join('x') };
  return out;

"""),
    },
    "style": {
        "note": "stylesheets, their rule counts, and whether a linked sheet reached the cascade",
        "probe": _probe("""
  var out = { sheets: [] };
  for (var i = 0; i < document.styleSheets.length; i++) {
    var sheet = document.styleSheets[i];
    var entry = { href: String(sheet.href || 'inline').slice(-30) };
    try { entry.rules = sheet.cssRules ? sheet.cssRules.length : '?'; }
    catch (e) { entry.rules = 'blocked'; }
    out.sheets.push(entry);
  }
  try {
    out.adopted = 'adoptedStyleSheets' in document
      ? document.adoptedStyleSheets.length : 'missing';
  } catch (e) { out.adopted = 'threw'; }
  var nodes = __pick(4);
  for (var j = 0; j < nodes.length; j++) { out[__tags(nodes[j])] = __brief(nodes[j]); }
  out.__computedMargin = __marginSupport();
  return out;

"""),
    },
    "ua": {
        "note": "the user-agent sheet: display, margins and padding of common tags",
        "fixture": (UA_PAGE, 0.6),
        "probe": _probe("""
  var out = {};
  var ids = ['marker','p','h1','h2','ul','ol','dl','blockquote','figure','pre','form',
             'fieldset','hr','table','header'];
  for (var i = 0; i < ids.length; i++) {
    var node = document.getElementById(ids[i]);
    if (!node) { out[ids[i]] = 'missing'; continue; }
    var brief = __brief(node);
    out[ids[i]] = [brief.box, brief.display].join(' | ');
  }
  out.__computedMargin = __marginSupport();
  return out;

"""),
    },
    "replaced": {
        "note": "default object size of replaced elements (300x150 and friends)",
        "fixture": (REPLACED_PAGE, 0.6),
        "probe": _probe("""
  var out = {};
  var ids = ['canvas','img','iframe','video','object','embed','svg','progress',
             'textarea','input','select','button'];
  for (var i = 0; i < ids.length; i++) {
    var brief = __brief(document.getElementById(ids[i]));
    out[ids[i]] = [brief.box, brief.display].join(' | ');
  }
  return out;

"""),
    },
    "rect": {
        "note": "what getBoundingClientRect returns, beyond the four numbers",
        "fixture": (RECT_PAGE, 0.6),
        "probe": """(function () {
  var out = {};
  var ids = ['box', 'hidden', 'frame', 'svg'];
  for (var i = 0; i < ids.length; i++) {
    var node = document.getElementById(ids[i]);
    try {
      var rect = node.getBoundingClientRect();
      out[ids[i]] = [Object.prototype.toString.call(rect),
                     String(rect.constructor.name),
                     typeof DOMRect === 'undefined' ? 'no-DOMRect' : (rect instanceof DOMRect),
                     Object.keys(rect).length,
                     typeof rect.toJSON === 'function' ? 'toJSON' : 'no-toJSON',
                     [rect.x, rect.y, rect.width, rect.height].join(',')].join(' | ');
    } catch (e) { out[ids[i]] = 'threw: ' + String(e).slice(0, 40); }
  }
  out.domRectProto = typeof DOMRect === 'undefined' ? 'missing'
    : Object.getOwnPropertyNames(DOMRect.prototype).join(',');
  out.getClientRects = String(typeof document.body.getClientRects);
  return out;
})()""",
    },
    "nodes": {
        "note": "element identity across creation paths (parse/create/template/clone/import/adopt/move/range)",
        "fixture": (NODES_PAGE, 0.6),
        "probe": """(function () {
  var out = { steps: window.__steps, range: window.__range, rangeRect: window.__rangeRect };
  for (var name in window.__nodes) {
    var node = window.__nodes[name];
    var text = [];
    try { text.push(String(node.tagName)); } catch (e) { text.push('threw'); }
    try { text.push(node.attributes ? node.attributes.length : 'no-map'); } catch (e) { }
    try { text.push('own=' + Object.getOwnPropertyNames(node).length); } catch (e) { }
    try { text.push(node.ownerDocument === document ? 'sameDoc' : 'otherDoc'); } catch (e) { }
    out[name] = text.join('|');
  }
  out.bodyContainsFirst = document.body.firstElementChild === window.__nodes.parsed;
  return out;
})()""",
    },
    "dom_api": {
        "note": "whether the common DOM entry points exist, and on what",
        "fixture": (RECT_PAGE, 0.6),
        "probe": """(function () {
  var names = ['adoptNode','importNode','createRange','createTreeWalker','createNodeIterator',
               'elementFromPoint','elementsFromPoint','createDocumentFragment',
               'insertAdjacentHTML','insertAdjacentElement','attachShadow','setHTMLUnsafe',
               'getHTML','moveBefore','replaceChildren','requestPointerLock',
               'setPointerCapture','animate','checkVisibility','computedStyleMap',
               'getSelection','getComputedStyle','structuredClone'];
  var kinds = { document: document, element: document.body,
                implementation: document.implementation,
                text: document.createTextNode('t'), window: window };
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
    "frames": {
        "note": "animation-frame pacing: same-turn stamps, intervals, frames per run",
        "fixture": (FRAMES_PAGE, 3.0),
        "probe": """(function () {
  var frames = window.__frames, gaps = frames.gaps;
  return { sameTurnSharesStamp: frames.sameTurn,
           first: Math.round(frames.first * 10) / 10,
           frames: frames.count,
           minGap: gaps.length ? Math.min.apply(null, gaps) : 0,
           maxGap: gaps.length ? Math.max.apply(null, gaps) : 0,
           distinctGaps: (function () {
             var seen = {}, out = 0;
             for (var i = 0; i < gaps.length; i++) {
               if (!seen[gaps[i]]) { seen[gaps[i]] = 1; out++; } }
             return out; })() };
})()""",
    },
    "timing": {
        "note": "clock readings: resolution, monotonicity, timeOrigin against Date.now",
        "fixture": (RECT_PAGE, 0.6),
        "probe": """(function () {
  var out = {};
  try {
    var readings = [], deltas = {};
    for (var i = 0; i < 4000; i++) {
      var value = performance.now();
      readings.push(value);
      var step = Math.round(value * 1000000) % 100000;
      deltas[step] = (deltas[step] || 0) + 1;
    }
    var steps = Object.keys(deltas).sort(function (a, b) { return deltas[b] - deltas[a]; });
    out.mostCommonMicroOffsets = steps.slice(0, 4).join(',');
    out.distinctOffsets = steps.length;
    out.monotonic = readings.every(function (value, index) {
      return index === 0 || value >= readings[index - 1]; });
    out.timeOriginSkewMs = Math.round(performance.timeOrigin + performance.now() - Date.now());
    out.timeOriginIsNumber = String(typeof performance.timeOrigin);
    out.dateNowResolutionMs = (function () {
      var seen = {}, count = 0;
      for (var j = 0; j < 20000; j++) { var now = Date.now();
        if (!seen[now]) { seen[now] = 1; count++; } }
      return count; })();
  } catch (e) { out.error = String(e).slice(0, 60); }
  return out;
})()""",
    },
    "globals": {
        "note": "a slice of the fingerprint surface: navigator/screen/window members",
        "probe": """(function () {
  var surface = function (target, names) {
    var out = {};
    for (var i = 0; i < names.length; i++) {
      try {
        var value = target[names[i]];
        out[names[i]] = value === null ? 'null'
          : typeof value === 'object' ? Object.prototype.toString.call(value)
          : typeof value === 'function' ? 'function'
          : String(value).slice(0, 40);
      } catch (e) { out[names[i]] = 'threw'; }
    }
    return out;
  };
  var navigatorNames = ['userAgent','platform','language','languages','hardwareConcurrency',
    'deviceMemory','maxTouchPoints','webdriver','vendor','product','onLine','cookieEnabled',
    'doNotTrack','plugins','mimeTypes','pdfViewerEnabled','userAgentData','storage',
    'credentials','permissions','geolocation','mediaDevices','clipboard','locks','gpu',
    'connection','webkitTemporaryStorage','serviceWorker','scheduling','userActivation'];
  var screenNames = ['width','height','availWidth','availHeight','colorDepth','pixelDepth',
    'orientation','isExtended'];
  var windowNames = ['innerWidth','innerHeight','outerWidth','outerHeight','devicePixelRatio',
    'screenX','screenY','isSecureContext','crossOriginIsolated','origin','name','length',
    'closed','status','frameElement','visualViewport','performance','crypto','indexedDB',
    'localStorage','sessionStorage','caches','crossOriginIsolated'];
  return { navigator: surface(navigator, navigatorNames),
           screen: surface(screen, screenNames),
           window: surface(window, windowNames),
           typeofs: { webkitURL: typeof window.webkitURL,
                      WebKitCSSMatrix: typeof window.WebKitCSSMatrix,
                      webkitStorageInfo: typeof window.webkitStorageInfo,
                      chrome: typeof window.chrome,
                      DOMRect: typeof window.DOMRect,
                      queryLocalFonts: typeof window.queryLocalFonts,
                      showOpenFilePicker: typeof window.showOpenFilePicker } };
})()""",
    },
    "surface": {
        "note": "every member of the main DOM/BOM objects by kind (accessor, value, function arity)",
        "fixture": (RECT_PAGE, 0.6),
        "probe": """(function () {
  //: Enumerated, never hand-listed: a list only confirms what someone already thought of.
  //: Descriptors only, so no getter runs and the page is undisturbed.
  var describe = function (target) {
    var out = {}, walk = target, depth = 0;
    try {
      while (walk && depth < 8) {
        var names = Object.getOwnPropertyNames(walk);
        for (var i = 0; i < names.length; i++) {
          var name = names[i];
          if (Object.prototype.hasOwnProperty.call(out, name)) continue;
          var d = Object.getOwnPropertyDescriptor(walk, name);
          if (!d) continue;
          var kind;
          if (d.get || d.set) kind = (d.get ? 'get' : '') + (d.set ? '/set' : '');
          else if (typeof d.value === 'function') kind = 'fn:' + d.value.length;
          else kind = 'value:' + (d.value === null ? 'null' : typeof d.value);
          out[name] = kind + (d.enumerable ? '' : ' !enum');
        }
        //: The four labelled symbols: string names alone leave them uncompared.
        var symbols = Object.getOwnPropertySymbols(walk);
        for (var j = 0; j < symbols.length; j++) {
          var symbol = symbols[j];
          var label = symbol === Symbol.iterator ? '@@iterator'
            : symbol === Symbol.asyncIterator ? '@@asyncIterator'
            : symbol === Symbol.toStringTag ? '@@toStringTag'
            : symbol === Symbol.toPrimitive ? '@@toPrimitive' : null;
          if (!label || Object.prototype.hasOwnProperty.call(out, label)) continue;
          var sd = Object.getOwnPropertyDescriptor(walk, symbol);
          if (!sd) continue;
          out[label] = (sd.value === undefined ? 'accessor'
            : typeof sd.value === 'function' ? 'fn:' + sd.value.length
            : 'value:' + (sd.value === null ? 'null' : typeof sd.value))
            + (sd.enumerable ? '' : ' !enum');
        }
        walk = Object.getPrototypeOf(walk);
        depth++;
      }
    } catch (e) { out['__walkError'] = String(e).slice(0, 80); }
    return out;
  };
  var holders = {};
  var add = function (label, target) {
    try { holders[label] = target ? describe(target) : { __missing: 'no such object' }; }
    catch (e) { holders[label] = { __error: String(e).slice(0, 80) }; }
  };
  add('window', window);
  add('document', document);
  add('document.body', document.body);
  add('navigator', navigator);
  add('screen', screen);
  add('location', location);
  add('history', history);
  add('performance', performance);
  add('crypto', crypto);
  add('localStorage', localStorage);
  add('sessionStorage', sessionStorage);
  add('indexedDB', indexedDB);
  var canvas = document.createElement('canvas');
  add('canvas', canvas);
  add('canvas.2d', canvas.getContext('2d'));
  add('event', new Event('x'));
  add('xhr', new XMLHttpRequest());
  var iframe = document.createElement('iframe');
  add('iframe', iframe);
  add('iframe.contentWindow', iframe.contentWindow);
  var constructors = ['Node','Element','HTMLElement','HTMLDocument','Document','Window',
    'Navigator','XMLHttpRequest','Worker','WebSocket','EventSource','Response','Request','Headers',
    'Blob','File','FormData','URLSearchParams','URL','Promise','TextEncoder','TextDecoder','Event',
    'CustomEvent','DOMException','MutationObserver','ResizeObserver','IntersectionObserver',
    'MessageChannel','MessagePort','BroadcastChannel','AbortController','PerformanceObserver',
    'WorkerNavigator',
    //: Namespaces: Chrome reports these as plain objects, and describing them shows a shape
    //: difference (``length``/``name``/``prototype`` exist only on a function).
    'CSS','GPUBufferUsage','GPUColorWrite','GPUMapMode','GPUShaderStage','GPUTextureUsage'];
  for (var i = 0; i < constructors.length; i++) {
    var ctor = window[constructors[i]];
    add(constructors[i], ctor);
    add(constructors[i] + '.prototype', ctor && ctor.prototype);
  }
  return holders;
})()""",
    },
    "surface_wide": {
        "note": "every member of every interface the window exposes, by kind (the whole surface)",
        "fixture": (RECT_PAGE, 0.6),
        "probe": """(function () {
  //: The whole surface, not a curated list: every name the window exposes that is a function or
  //: an object, with its ``prototype``.  Descriptors only, so the page is undisturbed.
  var describe = function (target) {
    var out = {}, walk = target, depth = 0;
    try {
      while (walk && depth < 8) {
        var names = Object.getOwnPropertyNames(walk);
        for (var i = 0; i < names.length; i++) {
          var name = names[i];
          if (Object.prototype.hasOwnProperty.call(out, name)) continue;
          var d = Object.getOwnPropertyDescriptor(walk, name);
          if (!d) continue;
          var kind;
          if (d.get || d.set) kind = (d.get ? 'get' : '') + (d.set ? '/set' : '');
          else if (typeof d.value === 'function') kind = 'fn:' + d.value.length;
          else kind = 'value:' + (d.value === null ? 'null' : typeof d.value);
          out[name] = kind + (d.enumerable ? '' : ' !enum');
        }
        //: Symbols under a stable ASCII label: ``getOwnPropertyNames`` never lists them.  Only
        //: the ones worth comparing are labelled -- an unlabelled symbol reads as a difference
        //: between the engines when it is only two names for itself.
        var symbols = Object.getOwnPropertySymbols(walk);
        for (var j = 0; j < symbols.length; j++) {
          var symbol = symbols[j];
          var label = symbol === Symbol.iterator ? '@@iterator'
            : symbol === Symbol.asyncIterator ? '@@asyncIterator'
            : symbol === Symbol.toStringTag ? '@@toStringTag'
            : symbol === Symbol.toPrimitive ? '@@toPrimitive' : null;
          if (!label || Object.prototype.hasOwnProperty.call(out, label)) continue;
          var sd = Object.getOwnPropertyDescriptor(walk, symbol);
          if (!sd) continue;
          out[label] = (sd.value === undefined ? 'accessor'
            : typeof sd.value === 'function' ? 'fn:' + sd.value.length
            : 'value:' + (sd.value === null ? 'null' : typeof sd.value))
            + (sd.enumerable ? '' : ' !enum');
        }
        walk = Object.getPrototypeOf(walk);
        depth++;
      }
    } catch (e) { out['__walkError'] = String(e).slice(0, 80); }
    return out;
  };
  //: The window under its self-referencing names: describing it once is enough.
  var SELF = {window: 1, self: 1, top: 1, parent: 1, frames: 1, globalThis: 1};
  var holders = {};
  var add = function (label, target) {
    try { if (label in holders) return; holders[label] = target ? describe(target) : { __missing: 'no such object' }; }
    catch (e) { holders[label] = { __error: String(e).slice(0, 80) }; }
  };
  var names = Object.getOwnPropertyNames(window).sort();
  for (var i = 0; i < names.length; i++) {
    var name = names[i];
    if (SELF[name]) continue;
    var value = null;
    try { value = window[name]; } catch (e) { continue; }
    if (typeof value === 'function') {
      add(name, value);
      var prototype = null;
      try { prototype = value.prototype; } catch (e) { prototype = null; }
      if (prototype) add(name + '.prototype', prototype);
    } else if (value && typeof value === 'object') {
      add(name, value);
      //: A frame's window answers nothing of its own to this page: what it is reached through
      //: is a facade, so its member list is not the other window's surface to compare.
      if (/^(0|[1-9][0-9]*)$/.test(name) && value.window === value) {
        holders[name]['__frameWindow'] = 'reached through a frame slot';
      }
    }
  }
  return holders;
})()""",
    },
    "events": {
        "note": "listener mechanics: order, once, capture, removal, dispatch return",
        "fixture": (RECT_PAGE, 0.6),
        "probe": """(function () {
  var out = {}, order = [];
  var host = document.createElement('div');
  document.body.appendChild(host);
  var onBubble = function () { order.push('bubble'); };
  var onCapture = function () { order.push('capture'); };
  var onOnce = function () { order.push('once'); };
  document.body.addEventListener('ping', onCapture, true);
  host.addEventListener('ping', onBubble);
  host.addEventListener('ping', onOnce, { once: true });
  var dispatched = host.dispatchEvent(new Event('ping', { bubbles: true }));
  host.dispatchEvent(new Event('ping', { bubbles: true }));
  document.body.removeEventListener('ping', onCapture, true);
  host.dispatchEvent(new Event('ping', { bubbles: true }));
  out.order = order.join(',');
  out.returnOfDispatch = String(dispatched);
  out.defaultPrevented = String(new Event('x', { cancelable: true }).preventDefault());
  var prevented = new Event('x', { cancelable: true });
  prevented.preventDefault();
  out.isPreventDefaulted = String(prevented.defaultPrevented);
  out.listenerCount = String(typeof getEventListeners);
  return out;
})()""",
    },
}
