"""Helpers shared by the browser object model modules; more than one module
needs each of them.
"""

from urllib.parse import urlsplit

from ..page_errors import SecurityError
from ..page_errors import js_error_value


def deny_cross_origin():
    raise SecurityError("Blocked a frame with origin from accessing a cross-origin frame")


def document_key(url):
    """The URL parts a same-document (fragment) navigation cannot change."""
    parts = urlsplit(url)
    return (parts.scheme, parts.netloc, parts.path, parts.query)


def is_secure_context(url):
    """Whether *url* is an origin a browser treats as potentially trustworthy."""
    text = str(url)
    # A blob (or filesystem) URL is as trustworthy as the origin it carries.
    if text[:5].lower() == "blob:":
        return is_secure_context(text[5:])
    parts = urlsplit(text)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if scheme in ("https", "wss", "file", "data"):
        return True
    if scheme in ("http", "ws"):
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")
    return False


#: How many creator hops to follow before calling it unknown.
CREATOR_HOPS = 8


def page_secure_context(page, url=None):
    """Whether a ``Page``'s document is in a secure context, its creator included.

    ``about:blank`` and ``about:srcdoc`` carry no scheme of their own, so a browser inherits the
    context from the document that created them: an inline frame inside an ``https`` page is a
    secure context, and Chrome exposes ``navigator.userActivation`` and its thirty relatives
    there.  ``Page.origin`` already inherits along this same ``parent_page`` link
    (``origin_tuple(..., inherited=...)``); the secure context has to follow it too, or the two
    disagree about the frame -- which is exactly the probe Cloudflare's widget makes (measured
    against Chrome 2026-10-06, see ``examples/5s/script/secure_frame.py``).

    ``url`` is for a commit still in flight, where the page has not taken the new address yet.
    """
    for _hop in range(CREATOR_HOPS):
        if page is None:
            return False
        text = str(url if url is not None else getattr(page, "url", "") or "")
        if urlsplit(text).scheme.lower() not in ("", "about"):
            return is_secure_context(text)
        url = None
        page = getattr(page, "parent_page", None)
    return False


def page_base_url(page, url=None):
    """The URL relative loads in a ``Page``'s document resolve against.

    ``about:blank`` and ``about:srcdoc`` carry no scheme of their own, so a browser takes the
    base URL from the document that created them -- which is why ``<script src="/cdn-cgi/.../jsd/main.js">``
    inside a frame still reaches the host that served the frame.  Resolving against the
    document's own address instead yields a bare path, and that path is then refused as "not
    a scheme a page loads", so nothing is requested at all (measured against Chrome
    2026-10-10, see ``examples/5s/script/base_url_frames.py``).

    Same walk as :func:`page_secure_context`, and the same reason: ``Page.origin`` already
    inherits along ``parent_page``, so resolution has to follow it too.  The document's own URL
    is left alone -- ``location.href`` still reads ``about:blank``.
    """
    for _hop in range(CREATOR_HOPS):
        if page is None:
            return str(url or "")
        text = str(url if url is not None else getattr(page, "url", "") or "")
        if urlsplit(text).scheme.lower() not in ("", "about"):
            return text
        url = None
        page = getattr(page, "parent_page", None)
    return str(url or "")


def window_secure_context(window, url=None):
    """``window``'s secure context, found through the realm's page rather than its parent.

    ``window.parent`` is an API-level accessor and a sandboxed frame has none to give: the
    creator is an *internal* link, and ``PageServices._page_realm.page`` is where
    ``ancestor_origins()`` already reads it from.
    """
    text = url
    if text is None:
        try:
            text = window.location.get_href()
        except BaseException:  # noqa: BLE001 - a window on its way out is not this question
            return False
    services = getattr(window, "_services", None)
    page = getattr(getattr(services, "_page_realm", None), "page", None)
    if page is None:
        return is_secure_context(text)
    return page_secure_context(page, text)


def settled(services, *, value=None, error=None):
    """A promise in this realm, already settled either way."""
    promise, resolve, reject = services.realm_services.make_promise()
    if error is None:
        resolve(value)
    else:
        reject(js_error_value(error))
    return promise



