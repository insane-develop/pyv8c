"""Host calls that run the page's own ``fetch`` and ``XMLHttpRequest``.

Reached through ``page.net.fetch``/``page.net.xhr``.  The realm's JavaScript performs
the request, so a page that replaced either API -- instrumentation, a test harness --
is the implementation that answers, and the page's own rules apply: CORS, the
resource interceptor, the cache.
"""
from __future__ import annotations

import json
from concurrent.futures import Future
from dataclasses import dataclass, field

from ..foundation.host_errors import LoadFailure


@dataclass
class JsHttpResponse:
    """What the page's ``fetch``/``XMLHttpRequest`` answered, read host-side."""

    url: str
    status: int
    status_text: str = ""
    headers: dict = field(default_factory=dict)
    body: str = ""
    redirected: bool = False

    @property
    def ok(self) -> bool:
        """Whether the status is a success status."""
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        """The body as text."""
        return self.body

    def json(self):
        """The body parsed as JSON, raising the usual ``json`` errors."""
        return json.loads(self.body)


#: It resolves with a JSON *string* -- a primitive crosses the binding without
#: conversion surprises -- and never rejects: a failure is a resolved
#: ``{ok: false, name, message}``.
_SNIPPET = """(async () => {
  const url = %(url)s;
  const init = %(init)s;
  try {
%(body)s
  } catch (error) {
    return JSON.stringify({ok: false, name: (error && error.name) || 'Error',
                           message: String((error && error.message) || error)});
  }
})()"""

_FETCH = """    const response = await fetch(url, init);
    const headers = [];
    if (response.headers && typeof response.headers.entries === 'function') {
      for (const pair of response.headers.entries()) headers.push([String(pair[0]), String(pair[1])]);
    }
    return JSON.stringify({ok: true, status: Number(response.status) || 0,
                           statusText: String(response.statusText == null ? '' : response.statusText),
                           url: String(response.url || url), redirected: !!response.redirected,
                           headers: headers, text: await response.text()});"""

_XHR = """    const answer = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.onreadystatechange = () => {
        if (xhr.readyState !== 4) return;
        resolve({status: Number(xhr.status) || 0,
                 statusText: String(xhr.statusText == null ? '' : xhr.statusText),
                 url: String(xhr.responseURL || url),
                 text: String(xhr.responseText == null ? '' : xhr.responseText)});
      };
      xhr.onerror = () => reject({name: 'LoadFailure', message: 'XMLHttpRequest failed'});
      xhr.open(%(method)s, url);
%(credentials)s
%(headers)s
      xhr.send(%(send)s);
    });
    return JSON.stringify({ok: true, status: answer.status, statusText: answer.statusText,
                           url: answer.url, headers: [], text: answer.text,
                           redirected: answer.url !== new URL(url, location.href).href});"""


def _fetch_init(options):
    """The ``RequestInit`` the page's ``fetch`` is called with."""
    init = {"method": str(options["method"] or "GET").upper()}
    if options["headers"]:
        init["headers"] = dict(options["headers"])
    if options["body"] is not None:
        init["body"] = str(options["body"])
    if options["credentials"]:
        init["credentials"] = str(options["credentials"])
    return init


def _xhr_headers(options):
    """The ``setRequestHeader`` calls one request's headers become."""
    return "\n".join(
        f"      xhr.setRequestHeader({json.dumps(str(name))}, {json.dumps(str(value))});"
        for name, value in (options["headers"] or {}).items()
    )


def _answer(value):
    """The snapshot object one resolved snippet carries, as a Python dict."""
    raw = value.to_python() if hasattr(value, "to_python") else value
    return raw if isinstance(raw, dict) else json.loads(raw)


def _source(kind, url, options):
    """The script one call evaluates; it resolves with the snapshot object."""
    if kind == "fetch":
        body, init = _FETCH, json.dumps(_fetch_init(options))
    else:
        body = _XHR % {
            "method": json.dumps(str(options["method"] or "GET").upper()),
            "credentials": ("      xhr.withCredentials = true;"
                            if str(options["credentials"] or "") == "include" else ""),
            "headers": _xhr_headers(options),
            "send": "" if options["body"] is None else json.dumps(str(options["body"])),
        }
        init = "null"
    return _SNIPPET % {"url": json.dumps(str(url)), "init": init, "body": body}


class JsHttpController:
    """Runs one page's ``fetch``/``XMLHttpRequest`` on behalf of the host."""

    def __init__(self, page):
        self._page = page

    def fetch(self, url, *, method="GET", headers=None, body=None, credentials=None,
              timeout=30) -> JsHttpResponse:
        """Run the page's ``fetch`` and wait for it to settle."""
        return self._await("fetch", url, self._options(method, headers, body, credentials), timeout)

    def fetch_async(self, url, *, method="GET", headers=None, body=None,
                    credentials=None) -> Future:
        """Start the page's ``fetch``; the ``Future`` settles on its answer."""
        return self._future("fetch", url, self._options(method, headers, body, credentials))

    def xhr(self, url, *, method="GET", headers=None, body=None, credentials=None,
            timeout=30) -> JsHttpResponse:
        """Run the page's ``XMLHttpRequest`` and wait for it to settle."""
        return self._await("xhr", url, self._options(method, headers, body, credentials), timeout)

    def xhr_async(self, url, *, method="GET", headers=None, body=None,
                  credentials=None) -> Future:
        """Start the page's ``XMLHttpRequest``; the ``Future`` settles on its answer."""
        return self._future("xhr", url, self._options(method, headers, body, credentials))

    @staticmethod
    def _options(method, headers, body, credentials):
        return {"method": method, "headers": headers, "body": body, "credentials": credentials}

    def _start(self, source, deliver):
        """Run *source* on the owner thread and hand its snapshot to *deliver*.

        The snippet's promise is what carries the answer: attaching the callback
        here needs no global of the page's, and a page that replaced ``fetch`` or
        ``XMLHttpRequest`` is the implementation the snippet reaches.
        """
        page = self._page
        promise = page.scripts.evaluate(source)
        promise.then(lambda payload: deliver(_answer(payload)))

    def _await(self, kind, url, options, timeout):
        """The snippet's answer, after turning this realm's loop until it arrives."""
        page = self._page
        answer = {}

        def run():
            self._start(_source(kind, url, options),
                        lambda payload: answer.update(payload=payload))
            page.tasks.pump_until(lambda: "payload" in answer, timeout=timeout)

        page.realm.executor.call(run)
        if "payload" not in answer:
            raise TimeoutError(f"the page's {kind}({url}) did not settle in {timeout} seconds")
        return self._response(url, answer["payload"])

    def _future(self, kind, url, options):
        """A ``Future`` the realm queue settles when the snippet answers."""
        page = self._page
        future = Future()

        def deliver(payload):
            if not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(self._response(url, payload))
            except BaseException as exc:
                future.set_exception(exc)

        try:
            page.realm.executor.call(lambda: self._start(_source(kind, url, options), deliver))
        except BaseException as exc:
            future.set_exception(exc)
        return future

    @staticmethod
    def _response(url, payload):
        """The snapshot object one answer becomes, or the error it carries."""
        if not payload.get("ok"):
            raise LoadFailure(f"{payload.get('name', 'Error')}: {payload.get('message', '')}")
        headers = {str(name): str(value) for name, value in payload.get("headers") or ()}
        return JsHttpResponse(
            url=str(payload.get("url") or url),
            status=int(payload.get("status") or 0),
            status_text=str(payload.get("statusText") or ""),
            headers=headers,
            body=str(payload.get("text") or ""),
            redirected=bool(payload.get("redirected")),
        )
