"""Read a run's log, or trace the engine while the page runs.

    python script/trace.py log                 # stub hits, errors and sources in ../px3.log
    python script/trace.py members             # which IDL members the page touched, and what they answered
    python script/trace.py window contentDocument
    python script/trace.py frames              # iframe contexts created/discarded, and their documents
    python script/trace.py style               # stylesheet discoveries and completions
    python script/trace.py binding             # interface materialisations, and when a wrapper turns flat
    python script/trace.py watch 30            # readings of the challenge frame, in both engines

``log``, ``members`` and ``window`` read a log produced by ``../px3.py --trace``.
"""
import collections
import pathlib
import re
import sys
import time

from common import URL, here, launch, chrome_page, snapshot, there

LOG = pathlib.Path(__file__).resolve().parent.parent / "px3.log"
TOP = re.compile(r"\[TOP\] (\w+): ([\w.]+)(?:\(.*?\))? -> (.*)$")

WATCH_HOOK = r"""
window.__watch = { seen: [], broken: null, members: null };
(function () {
  var probe = function (where) {
    try {
      if (window.__watch.broken) { return; }
      var node = document.querySelector('#px-captcha iframe');
      if (!node) { return; }
      var state = [String(node.tagName), node.attributes ? node.attributes.length : 'attrs?',
                   node.parentNode ? 'parent' : 'parent?',
                   node.ownerDocument === document ? 'sameDoc' : 'otherDoc',
                   typeof node.contentDocument].join('|');
      if (window.__watch.seen.length < 6) { window.__watch.seen.push(where + ' ' + state); }
      if (node.tagName === undefined || node.attributes === undefined) {
        window.__watch.broken = where;
        var members = {};
        var read = function (key, thunk) {
          try { members[key] = String(thunk()).slice(0, 26); }
          catch (e) { members[key] = 'threw'; }
        };
        read('nodeType', function () { return node.nodeType; });
        read('inTagName', function () { return 'tagName' in node; });
        read('keys', function () { return Object.keys(node).length; });
        read('own', function () { return Object.getOwnPropertyNames(node).length; });
        read('protoPrototype', function () {
          return Object.getPrototypeOf(node) === HTMLIFrameElement.prototype; });
        var names = Object.getOwnPropertyNames(node);
        members.ownSample = names.slice(0, 12).join(',');
        window.__watch.members = members;
      }
    } catch (e) { window.__watch.error = String(e).slice(0, 60); }
  };
  var wrap = function (proto, name) {
    var original = proto && proto[name];
    if (typeof original !== 'function') { return; }
    proto[name] = function () {
      var result = original.apply(this, arguments);
      probe(name);
      return result;
    };
  };
  wrap(Node.prototype, 'appendChild');
  wrap(Node.prototype, 'insertBefore');
  wrap(Node.prototype, 'removeChild');
  wrap(Node.prototype, 'replaceChild');
  wrap(Element.prototype, 'setAttribute');
  wrap(Document.prototype, 'adoptNode');
  wrap(Document.prototype, 'importNode');
  wrap(Document.prototype, 'write');
  setInterval(function () { probe('timer'); }, 500);
})();
"""

WATCH_REPORT = ("({seen: window.__watch.seen, broken: window.__watch.broken, "
                "members: window.__watch.members, error: window.__watch.error})")


def log_lines(path):
    return [line for line in pathlib.Path(path).read_text(encoding="utf-8",
                                                         errors="replace").splitlines()
            if "engine.trace" in line]


def show_log(path):
    lines = pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    print("lines:", len(lines))
    sources = collections.Counter()
    for line in lines:
        match = re.search(r"\| [A-Z]+ +\| (\S+?) - ", line)
        if match:
            sources[match.group(1).split(":")[0]] += 1
    print("--- sources")
    for source, count in sources.most_common(10):
        print(f"{count:6d}  {source}")
    stubs = collections.Counter(re.findall(r"pyv8c: (.+?) 待实现", "\n".join(lines)))
    print(f"--- 待实现: members={len(stubs)} hits={sum(stubs.values())}")
    for member, hits in stubs.most_common(15):
        print(f"{hits:5d}  {member}")
    for marker in ("ERROR", "WARNING", "Uncaught", "not a function", "is not defined"):
        found = [line for line in lines if marker in line]
        if found:
            print(f"--- {marker} ({len(found)})")
            for line in found[:5]:
                print("   ", line.strip()[:170])


def show_members(path):
    verbs = collections.Counter()
    answers = collections.defaultdict(collections.Counter)
    for line in log_lines(path):
        match = TOP.search(line.strip())
        if not match:
            continue
        verb, member, answer = match.group(1), match.group(2).split(".")[-1], match.group(3)
        verbs[(member, verb)] += 1
        answers[member][answer[:36]] += 1
    print("--- most touched members")
    for (member, verb), hits in verbs.most_common(20):
        print(f"{hits:6d} {verb:10} {member}")
    print("--- answers for the challenge frame's members")
    for member in ("contentDocument", "contentWindow", "appendChild", "innerHTML", "src"):
        if answers.get(member):
            top = answers[member].most_common(3)
            print(f"    {member:16} " + "  ".join(f"{value!r} x{count}" for value, count in top))


def show_window(path, needle, before=12, after=30):
    lines = log_lines(path)
    hits = [index for index, line in enumerate(lines) if needle in line]
    print(f"trace lines: {len(lines)}  hits for {needle!r}: {len(hits)}")
    if not hits:
        return
    for where in {hits[0], hits[-1]}:
        print(f"--- around hit {where}")
        for line in lines[max(0, where - before):where + after]:
            body = line.split(" - ", 1)[-1]
            if "[TOP]" in body:
                print("   ", body[:150])


def trace_frames(seconds):
    """iframe contexts as they are created and discarded, and their documents."""
    from pyv8c.platform.resource_loading import ResourceController
    seen, events = [], []
    ensure, discard = (ResourceController.ensure_iframe_context,
                      ResourceController.discard_iframe_context)

    def describe(node):
        services = getattr(node, "_child_services", None)
        document = getattr(getattr(services, "document_services", None), "document", None)
        body = document.get_body() if document is not None and hasattr(document, "get_body") else None
        children = len(getattr(body, "_children", ())) if body is not None else None
        text = ""
        if body is not None:
            try:
                text = " ".join(str(body.get_textContent() or "").split())[:160]
            except Exception as error:  # noqa: BLE001
                text = f"<{type(error).__name__}>"
        return {"attrs": sorted(getattr(node, "attributes", {}) or {}),
                "context": services is not None,
                "url": str(getattr(document, "url", ""))[:48],
                "bodyChildren": children, "text": text}

    def traced_ensure(self, iframe):
        before = describe(iframe)
        result = ensure(self, iframe)
        if iframe not in seen:
            seen.append(iframe)
        events.append(("ensure", before["context"], describe(iframe)["context"]))
        return result

    def traced_discard(self, iframe):
        result = discard(self, iframe)
        events.append(("discard", True, False))
        return result

    ResourceController.ensure_iframe_context = traced_ensure
    ResourceController.discard_iframe_context = traced_discard

    from pyv8c import Browser
    browser = Browser(stub_logging="off", threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        page.goto(URL)
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        print("events:", events)
        for iframe in seen:
            print("   ", describe(iframe))
    finally:
        browser.close()


def trace_binding(seconds):
    """IdlInterface materialisations, and whether the frame's wrapper stays healthy."""
    from pyv8c.platform.sessions import BindingSession
    log = []
    for attribute in ("materialize", "class_config_for", "_stage"):
        original = getattr(BindingSession, attribute)

        def traced(self, name, _original=original, _attribute=attribute):
            log.append((time.time(), hex(id(self)), _attribute, str(name)[:34]))
            return _original(self, name)

        setattr(BindingSession, attribute, traced)

    health = ("(function () { var node = document.querySelector('#px-captcha iframe');"
              " if (!node) { return 'absent'; } return (node.tagName === undefined ? 'flat' : 'ok')"
              " + ':' + Object.getOwnPropertyNames(node).length; })()")
    from pyv8c import Browser
    browser = Browser(stub_logging="off", threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        page.goto(URL)
        started, marks, flip = time.time(), [], None
        while time.time() - started < seconds:
            try:
                page.run_until_idle(timeout=0)
            except Exception:  # noqa: BLE001
                pass
            state = snapshot(page, health)
            if not marks or marks[-1][1] != state:
                marks.append((time.time(), state))
                if isinstance(state, str) and state.startswith("flat"):
                    flip = time.time()
            time.sleep(0.2)
        print("marks:", [(round(moment - started, 1), state) for moment, state in marks[-8:]])
        if flip:
            print("--- materialisations in the second before the flip")
            for moment, session, attribute, name in log:
                if flip - 1.0 <= moment <= flip:
                    print(f"    {round(moment - started, 1)}s {session} {attribute:16} {name}")
        counts = collections.Counter((session, name) for _, session, _, name in log)
        print("--- most requested specs")
        for (session, name), count in counts.most_common(8):
            print(f"    {count:4} {session} {name}")
    finally:
        browser.close()


def trace_style(seconds):
    """Stylesheet resources as they are discovered and completed."""
    from pyv8c.platform.resource_loading import ResourceController
    log = []
    complete = ResourceController.complete_document_resource
    discover = ResourceController.discover_element_resource

    def traced_complete(self, token, generation, response, error, handle=None):
        item = self.resources.get(token)
        node = item[1] if item else None
        if str(getattr(node, "localName", "")) == "link":
            log.append(("complete", str(node.attributes.get("href", ""))[-36:],
                        type(error).__name__ if error else "ok",
                        len(response.body_text()) if response is not None else 0))
        return complete(self, token, generation, response, error, handle)

    def traced_discover(self, node):
        if str(getattr(node, "localName", "")) == "link":
            log.append(("discover", str(node.attributes.get("rel", "")),
                        str(node.attributes.get("href", ""))[-36:], 0))
        return discover(self, node)

    ResourceController.complete_document_resource = traced_complete
    ResourceController.discover_element_resource = traced_discover

    from pyv8c import Browser
    browser = Browser(stub_logging="off", threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        page.goto(URL)
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        print("sheets:", snapshot(page,
                                 "(function () { var out = [];"
                                 " for (var i = 0; i < document.styleSheets.length; i++) {"
                                 "   var sheet = document.styleSheets[i];"
                                 "   out.push(String(sheet.href || 'inline').slice(-22) + ':' +"
                                 "   (sheet.cssRules ? sheet.cssRules.length : '?')); }"
                                 " return out.join(' '); })()"))
    finally:
        browser.close()
    for entry in log:
        print("   ", entry)


def watch(seconds):
    """Readings of the challenge frame as the page builds it, both engines."""
    import curl_cffi.requests
    from pyv8c import Browser
    done = {"document": False}

    def intercept(url, request):
        if done["document"] or request.get("destination") != "document":
            return None
        done["document"] = True
        answer = curl_cffi.requests.get(str(url), impersonate="chrome")
        html = answer.text.replace("<head>", "<head><script>" + WATCH_HOOK + "</script>", 1)
        return {"status": answer.status_code, "body": html, "url": str(url),
                "headers": dict(answer.headers)}

    browser = Browser(stub_logging="off", resource_interceptor=intercept,
                      threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        page.goto(URL)
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        print("=== here")
        for key, value in snapshot(page, WATCH_REPORT).items():
            print(f"    {key:9} {value}")
    finally:
        browser.close()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        chrome = launch(playwright)
        try:
            page = chrome_page(chrome)
            page.add_init_script(WATCH_HOOK)
            page.goto(URL, wait_until="load", timeout=60000)
            page.wait_for_timeout(seconds * 1000)
            print("=== chrome")
            for key, value in snapshot(page, WATCH_REPORT).items():
                print(f"    {key:9} {value}")
        finally:
            chrome.close()


def main(argv):
    mode = argv[0] if argv else "log"
    rest = argv[1:]
    if mode == "log":
        show_log(rest[0] if rest else LOG)
    elif mode == "members":
        show_members(rest[0] if rest else LOG)
    elif mode == "window":
        show_window(rest[1] if len(rest) > 1 else LOG, rest[0] if rest else "contentDocument")
    elif mode == "frames":
        trace_frames(float(rest[0]) if rest else 35.0)
    elif mode == "binding":
        trace_binding(float(rest[0]) if rest else 40.0)
    elif mode == "style":
        trace_style(float(rest[0]) if rest else 30.0)
    elif mode == "watch":
        watch(float(rest[0]) if rest else 30.0)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
