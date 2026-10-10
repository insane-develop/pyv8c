"""SteamDB under pyv8c, judged only by the cookie we are after.

The judging run -- the one that reports the exchange table, the jar and the verdict.  Its
one-line counterpart is ``examples/5s/5s.py``; this is the tool that says *where* a run was
lost, and both write the same ``5s.log`` beside the case.

    python script/5s.py                # quiet run: exchange table, jar, verdict
    python script/5s.py --seconds 45   # let the challenge live longer
    python script/5s.py --trace        # engine trace and verbose stubs -> 5s.log
    python script/5s.py --timeout 150  # hard kill for the child when a task wedges
    python script/5s.py --no-watch     # without the transport witness

**Verdict**: the context's cookie jar holds ``cf_clearance`` for ``steamdb.info`` and the page
is SteamDB itself rather than the interstitial.

The run reports three witnesses and nothing else, because these are the three a challenge can
be argued from:

* **exchange table** -- every request the transport made, its status, its size, and the
  ``Set-Cookie`` names it carried.  ``cf_clearance`` *is* a ``Set-Cookie``, so a run that never
  looks at one cannot say where it was lost;
* **cookie jar** -- what we kept, with domains;
* **page state** -- where we ended, and what the document says.

The page's challenge runs long tasks of its own, so ``run_until_idle`` cannot time it out: the
work ends when the script returns.  The real run therefore happens in a child process with a
hard kill -- a wedged realm must not wedge the analysis.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

URL = "https://steamdb.info/"
#: The log stays beside the case, not beside the tools: the simple ``../5s.py`` writes the same
#: file, and the README's judge commands read it from there.
LOG = Path(__file__).resolve().parent.parent / "5s.log"

#: Every exchange of this run: ``(method, url, status, bytes, cookie names, body snippet)``.
EXCHANGES = []

#: Every ``Set-Cookie`` the jar was handed: ``(url, header, names kept)``.
JAR_EVENTS = []

#: What the page says about itself, including the challenge's own bookkeeping.
SNAPSHOT = r"""
(function () {
  var opt = window._cf_chl_opt || {};
  function frame_text(frame) {
    try {
      var doc = frame.contentDocument;
      if (!doc) { return frame.getAttribute('sandbox') ? 'cross-origin' : 'no document'; }
      return String((doc.body && doc.body.innerText) || doc.documentElement.textContent || '')
              .replace(/\s+/g, ' ').trim().slice(0, 120);
    } catch (error) { return 'unreadable: ' + error.name; }
  }
  return {
    url: location.href.slice(0, 120),
    title: String(document.title || '').slice(0, 80),
    body: String((document.body && document.body.innerText) || '')
            .replace(/\s+/g, ' ').trim().slice(0, 120),
    frames: [].map.call(document.querySelectorAll('iframe'), function (f) {
      return { sandbox: String(f.getAttribute('sandbox') || '-').slice(0, 40),
               src: String(f.src || '').slice(0, 40),
               text: frame_text(f) }; }),
    scripts: document.querySelectorAll('script').length,
    chl_opt: Object.keys(opt).slice(0, 14),
    turnstile: window.turnstile ? Object.keys(window.turnstile).slice(0, 8) : null,
    widget: (function () {
      var node = document.querySelector('[class*=cf-], #cf-chl-widget, [id*=turnstile]');
      if (!node) { return null; }
      var attrs = {};
      [].forEach.call(node.attributes || [], function (a) { attrs[a.name] = String(a.value).slice(0, 60); });
      return { tag: node.tagName, id: node.id, cls: String(node.className).slice(0, 60),
               text: String(node.innerText || node.textContent || '')
                       .replace(/\s+/g, ' ').trim().slice(0, 300),
               attrs: attrs };
    })(),
    error: (function () {
      var node = document.querySelector('.cf-error');
      return node ? String(node.innerText || node.textContent || '')
                      .replace(/\s+/g, ' ').trim().slice(0, 400) : null;
    })()
  };
})()
"""


def _text(value):
    """A request body as text, whatever shape the caller built it in."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def watch_calls():
    """Print the traceback of the engine calls the widget catches and reports.

    The trace names the exception type only (``, error: <AttributeError>``), and an
    ``AttributeError`` is a Python defect rather than a DOM error: something in our own stack
    is missing.  Wrapping the two callbacks the widget uses for its "environment check" turns
    that into file and line.  Read-only: the original still runs, and still raises.
    """
    import traceback

    from pyv8c.web.bom.navigator import GPU
    from pyv8c.web.bom.webrtc import RTCPeerConnection

    def watched(owner, name, label):
        original = getattr(owner, name)

        def wrapper(self, *args, **kwargs):
            try:
                return original(self, *args, **kwargs)
            except BaseException:
                print(f"! {label} raised:")
                for entry in traceback.format_exc().splitlines()[-6:]:
                    print(f"      {entry.strip()[:150]}")
                raise

        setattr(owner, name, wrapper)

    watched(GPU, "fn_requestAdapter", "navigator.gpu.requestAdapter()")
    watched(RTCPeerConnection, "fn_createOffer", "RTCPeerConnection.createOffer()")


def watch_transport():
    """Record every exchange the network transport makes.

    This is the only place a ``Set-Cookie`` becomes visible: the jar stores what it is handed,
    so "the server never sent it" and "we dropped it" have to be told apart *here*, before the
    jar either way.  Read-only: the wrapped call is the original one.
    """
    from pyv8c.network.curl_cffi import CurlCffiTransport
    from pyv8c.network.cookies import split_set_cookie

    original = CurlCffiTransport._call

    def wrapped(self, request):
        started = time.perf_counter()
        #: What the jar held going *in*: a granted cookie that goes missing is either not sent
        #: here or deleted by this response, and the two need telling apart.
        before = _jar_names(self.cookies)
        response = original(self, request)
        header = str(response.headers.get("set-cookie") or "")
        names = ", ".join(sorted({part.split("=")[0].strip() for part in split_set_cookie(header)
                                  if "=" in part})) if header else ""
        body = str(getattr(response, "body", "") or "")
        host = str(getattr(request, "url", ""))
        sent = _text(getattr(request, "body", None))
        #: The sent body is what the widget *says*: ``eb/…/chl_api_message`` is how it reports
        #: an error to Cloudflare, and Chrome never makes that call -- so its words are the
        #: finding, not the status code.
        snippet = ""
        if "challenges.cloudflare.com" in host or len(body) <= 400:
            snippet = " ".join(body.split())[:150]
        EXCHANGES.append({
            "method": str(request.method).upper(), "url": host,
            "status": getattr(response, "status", "?"), "bytes": len(body),
            "cookies": names, "ms": int((time.perf_counter() - started) * 1000),
            "body": snippet, "jar": len(getattr(self.cookies, "_cookies", {}) or {}),
            "sent": len(sent), "sent_body": " ".join(sent.split())[:220],
            #: Which cookies the jar holds after this exchange, not just how many: a granted
            #: ``cf_clearance`` that a later response replaces has to be visible as a change.
            "jar_names": _jar_names(self.cookies),
            #: What we sent, not just where: ``pat``/``ci`` answering 400 while Chrome gets
            #: 401/200 says the edge rejected *our request*, and a header is the thing it can
            #: reject.  Values keep enough width to be compared with Chrome's -- a 44-character
            #: cut turned "which URL is the referrer" into a question the dump could not answer.
            "headers": "; ".join(f"{name}={str(value)[:160]}"
                                 for name, value in sorted(
                                     (dict(getattr(request, "headers", {}) or {})).items())),
            #: What came back, all of it: the widget reads ``cf-chl-out``/``cf-chl-out-s`` off a
            #: challenge response and continues only when they carry something, so whether the
            #: answer arrives with those headers is the difference that decides the last step.
            "resp_headers": "; ".join(f"{name}={str(value)[:80]}"
                                      for name, value in sorted(
                                          (dict(getattr(response, "headers", {}) or {})).items())),
            #: The header itself: a granted cookie the jar drops can only be replayed from the
            #: exact bytes the server sent.
            "raw_cookie": header[:220],
            "jar_before": before,
        })
        return response

    CurlCffiTransport._call = wrapped
    if CurlCffiTransport._call is not wrapped:
        #: A witness that quietly fails to install is worse than none: the table came out empty
        #: once because an edit left this assignment after a ``return``.
        raise RuntimeError("the transport witness was not installed")


def _jar_names(jar):
    """The cookie names the jar holds, sorted, for a row of the exchange table."""
    return ", ".join(sorted({name for (_domain, _path, name) in
                             dict(getattr(jar, "_cookies", {}) or {})}))


def watch_executor():
    """Print the traceback of a call the realm's owner thread never picked up.

    ``RealmBusyError`` says a realm did not answer in time, which is all the trace can tell: the
    caller in our own stack and the call that was still holding the thread are what say whether
    the two are waiting for each other.
    """
    import faulthandler
    import traceback

    from pyv8c.foundation.host_errors import RealmBusyError
    from pyv8c.foundation.executor import RealmExecutor

    original = RealmExecutor.call

    def wrapped(self, callback, *args, **kwargs):
        try:
            return original(self, callback, *args, **kwargs)
        except RealmBusyError as error:
            print(f"! realm busy: {error}")
            #: Both ends: the outer frames say who asked, the inner ones where the thread was
            #: held.  Printing only the tail once hid the caller entirely.
            lines = traceback.format_exc().splitlines()
            for entry in lines[:14] + (["      ..."] if len(lines) > 22 else []) + lines[-8:]:
                print(f"      {entry.strip()[:150]}")
            #: And every thread's stack: the realm that never answered is a *different* thread,
            #: so its own stack -- not this one -- says what it is holding.
            print("! thread dump:")
            faulthandler.dump_traceback()
            raise

    RealmExecutor.call = wrapped
    if RealmExecutor.call is not wrapped:
        raise RuntimeError("the executor witness was not installed")


def watch_jar():
    """Record every ``Set-Cookie`` the jar is handed, with what it kept of it.

    The exchange table shows *that* a granted cookie went missing; this shows *why*: the header
    the jar received, whole, next to the names it ended up holding.
    """
    from pyv8c.network.cookies import CookieJar

    original = CookieJar.add_from_response

    def wrapped(self, url, header):
        result = original(self, url, header)
        JAR_EVENTS.append((str(url), str(header), _jar_names(self)))
        return result

    CookieJar.add_from_response = wrapped
    if CookieJar.add_from_response is not wrapped:
        raise RuntimeError("the jar witness was not installed")


def jar_events():
    """Print what the jar was handed, and what it kept."""
    print(f"set-cookie headers ({len(JAR_EVENTS)}):")
    for url, header, names in JAR_EVENTS:
        print(f"    {url[:60]}")
        print(f"      given: {header[:400]}")
        print(f"      kept : [{names}]")


def exchanges_since(start, context):
    """Print the exchanges of this run, oldest first: what was asked and what came back."""
    print(f"exchanges ({len(EXCHANGES) - start}):")
    previous_jar = None
    for entry in EXCHANGES[start:]:
        jar_note = ""
        if entry["jar_names"] != previous_jar:
            jar_note = f" jar=[{entry['jar_before']} -> {entry['jar_names']}]"
            previous_jar = entry["jar_names"]
        print(f"    {entry['method']:4} {entry['url']} -> {entry['status']} "
              f"{entry['bytes']}B {entry['ms']}ms"
              + (f" set-cookie: {entry['cookies']}" if entry["cookies"] else "")
              + jar_note)
        #: The header line is what the two tables are compared on: where a request is refused
        #: (400 against Chrome's 401/200), the difference is in here, not in the URL.
        if entry["headers"]:
            print(f"         hdr : {entry['headers']}")
        if entry["resp_headers"]:
            print(f"         back: {entry['resp_headers']}")
        if entry["raw_cookie"]:
            print(f"         raw : {entry['raw_cookie']}")
        if entry["sent"]:
            print(f"         sent {entry['sent']}B: {entry['sent_body']}")
        if entry["body"]:
            print(f"         got : {entry['body'][:150]}")


def cookies_of(context):
    """Every cookie in the jar, as ``(name, domain, path, value)``."""
    jar = getattr(context, "cookies", None)
    stored = []
    for (domain, path, name), cookie in dict(getattr(jar, "_cookies", {}) or {}).items():
        stored.append((name, domain, path, str(cookie.value)[:24]))
    return stored


def evaluate_bounded(page, expression, seconds=15.0):
    """The page's reading, or why it could not be read in time.

    A realm busy in the challenge's own loop answers nothing for a minute -- and the snapshot is
    only one of the three witnesses, so the verdict must not wait for it.  The old shape spent
    60 s per call and left the run to the hard kill, which is why ``--seconds 60`` used to take
    over two minutes and print no verdict at all.
    """
    box = {}

    def read():
        try:
            box["value"] = page.evaluate(expression)
        except BaseException as error:  # noqa: BLE001
            box["error"] = f"{type(error).__name__}: {str(error)[:80]}"

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(seconds)
    if "value" in box:
        return box["value"]
    if "error" in box:
        return {"error": box["error"]}
    return {"error": f"no answer in {seconds:g}s (the realm is busy)"}


def report(context, page, started):
    """Print the witnesses and the verdict."""
    exchanges_since(started, context)
    jar_events()
    stored = cookies_of(context)
    print(f"cookies ({len(stored)}): {', '.join(sorted({entry[0] for entry in stored})) or '(none)'}")
    for name, domain, path, value in stored:
        print(f"    {name}@{domain}{path} = {value}")

    state = evaluate_bounded(page, SNAPSHOT)
    print(f"page: {json.dumps(state, ensure_ascii=False)}")

    clearance = [entry for entry in stored if entry[0] == "cf_clearance"]
    on_steamdb = str(state.get("url", "")).startswith("https://steamdb.info/") or \
        "Steam Database" in str(state.get("title", ""))
    challenge = "challenge" in str(state.get("url", "")) or \
        "Just a moment" in str(state.get("title", ""))
    if clearance and on_steamdb and not challenge:
        print("VERDICT: PASS -- cf_clearance held, and the page is SteamDB")
        return 0
    missing = []
    if not clearance:
        missing.append("no cf_clearance")
    if not on_steamdb:
        missing.append("not on SteamDB")
    if challenge:
        missing.append("still on the challenge document")
    print(f"VERDICT: FAIL -- {', '.join(missing)}")
    return 1


def browse(stub_logging, trace, log, seconds, watch):
    from loguru import logger
    from pyv8c import Browser

    #: The default sink writes every line to stderr as well, which buries the verdict: the
    #: run's stdout has to answer "did we get cf_clearance", nothing else.
    logger.remove()
    logger.add(log, level="DEBUG" if trace else "INFO", mode="w")
    logger.add(sys.stdout, level="WARNING")
    if watch:
        watch_transport()
        watch_jar()
        watch_executor()
        watch_calls()

    started = len(EXCHANGES)
    began = time.time()
    browser = Browser(stub_logging=stub_logging, trace=trace, traceable=trace,
                      threaded_loaders=True)
    context = browser.new_context()
    page = context.new_page()
    code = 1  # a run that dies on the way is a failed run, not a crash
    try:
        try:
            page.goto(URL)
        except BaseException as error:  # noqa: BLE001 - the challenge keeps working anyway
            print(f"goto: {type(error).__name__}: {str(error)[:100]}")
        pump(page, 5)
        #: The run is judged once the page has had its full time, not when it looks quiet.
        pump(page, max(0.0, seconds - 5))
        code = report(context, page, started)
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(10.0)
        print(f"ran {time.time() - began:.1f}s")
        sys.stdout.flush()
        #: A wedged realm must not outlive the run: the OS reclaims whatever is left.
        os._exit(code)


def pump(page, seconds=0.0, step=0.2):
    """Let the queue turn for *seconds*.

    A running challenge keeps its own timers going, so the page need not reach idle; a realm
    that is busy mid-task is simply retried.  Never raise from here: an error on the way is
    part of what the run is measuring.
    """
    deadline = time.time() + seconds
    while True:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001
            pass
        if time.time() >= deadline:
            return
        time.sleep(step)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--trace", action="store_true", help="verbose stubs and engine trace")
    #: 90, not 30: the challenge only submits ``cf_clearance`` late in its run -- with a 25-30s
    #: window every run looked like "the server never sent a cookie" (measured 2026-10-06).
    parser.add_argument("--seconds", type=float, default=90.0, help="seconds the page may live")
    parser.add_argument("--timeout", type=float, default=200.0, help="hard kill for the child")
    parser.add_argument("--log", type=Path, default=LOG, help="where the child logs")
    parser.add_argument("--no-watch", action="store_true",
                        help="without the transport witness (exchanges and Set-Cookie)")
    arguments = parser.parse_args(argv)

    if arguments.child:
        browse("verbose" if arguments.trace else "off", arguments.trace, arguments.log,
               arguments.seconds, not arguments.no_watch)
        return 0

    command = [sys.executable, "-u", __file__, "--child", "--log", str(arguments.log),
               "--seconds", str(arguments.seconds)]
    if arguments.trace:
        command.append("--trace")
    if arguments.no_watch:
        command.append("--no-watch")
    process = subprocess.Popen(command)
    try:
        code = process.wait(timeout=arguments.timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        print(f"killed after {arguments.timeout:.0f}s: the page never ran out of work",
              file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
