"""Redirect decisions every load path shares.

A browser follows 301, 302, 303, 307 and 308 itself rather than leaving it to a
transport: the decision needs the page's origin (a CORS check per hop), the context's
cookie jar (each hop's ``Set-Cookie``) and the request's redirect mode, none of which
an engine has.  What one hop becomes is a pure fact about the status, the method and
``Location``, so it lives here, depending on nothing but the URL primitives.

The table was observed on the wire from Chrome 154 rather than derived from the
specification: 301/302 rewrite POST to GET, 303 rewrites everything but HEAD, 307/308
keep method and body, twenty redirects are followed and the twenty-first fails, a
``Location`` that cannot be resolved fails while a missing one is delivered as it
arrived, and a ``manual`` redirect reads as ``opaqueredirect``.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..foundation.urls import absolute_url, same_origin

__all__ = [
    "DELIVERED",
    "OPAQUE",
    "REDIRECT_LIMIT",
    "REDIRECT_STATUSES",
    "BadLocation",
    "Deliver",
    "Follow",
    "RedirectFailure",
    "TooManyRedirects",
    "is_redirect",
    "location_text",
    "next_hop",
    "reportable_hops",
    "resolved_location",
]

#: The statuses a browser follows; every other 3xx is delivered as it arrived.
REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})

#: Chrome follows twenty redirects and fails the twenty-first.
REDIRECT_LIMIT: int = 20

#: What a hop must not carry across origins (fetch's CORS non-wildcard header names).
_ACROSS_ORIGIN_DROPPED: frozenset[str] = frozenset({"authorization"})

#: What is dropped with the body when a redirect turns the request into a GET.
_BODY_HEADERS: frozenset[str] = frozenset({"content-type", "content-length"})


class RedirectFailure(Exception):
    """A redirect a load path reports to the page as a network error."""


class TooManyRedirects(RedirectFailure):
    """The chain ran past :data:`REDIRECT_LIMIT`."""

    def __init__(self, limit: int = REDIRECT_LIMIT):
        super().__init__(f"more than {limit} redirects")
        self.limit = limit


class BadLocation(RedirectFailure):
    """``Location`` cannot name a URL a request could be made to."""

    def __init__(self, location):
        super().__init__(f"cannot resolve redirect location {str(location)!r}")
        self.location = location


@dataclass(frozen=True)
class Follow:
    """The next hop a redirect asks for, method and body already rewritten."""

    url: str
    method: str
    body: object
    headers: dict[str, str]
    cross_origin: bool


@dataclass(frozen=True)
class Deliver:
    """Deliver the response as it arrived; a manual redirect reads as opaque."""

    opaque: bool = False


#: The two decisions, for callers that compare rather than unpack.
DELIVERED = Deliver()
OPAQUE = Deliver(opaque=True)


def reportable_hops(hops: int, crossed: bool) -> int:
    """The ``redirectCount`` a navigation reports: zero once a hop crossed origins.

    A same-origin chain is counted hop by hop, while a cross-origin one reports
    nothing at all, which is how Chrome keeps a redirect from saying how another
    origin answered it.
    """
    return 0 if crossed else int(hops)


def is_redirect(status) -> bool:
    """Whether a browser follows this response status."""
    try:
        return int(status) in REDIRECT_STATUSES
    except (TypeError, ValueError):
        return False


def location_text(location) -> str:
    """One ``Location``, however a transport spelled it: cronet repeats headers as lists."""
    if isinstance(location, (list, tuple)):
        location = location[0] if location else ""
    return "" if location is None else str(location).strip()


def resolved_location(location, base):
    """The absolute URL *location* names, or ``None`` when it cannot be one.

    A relative ``Location`` resolves against the hop that carried it, which is what
    makes ``Location: ../next`` work in a browser.
    """
    text = location_text(location)
    if not text:
        return None
    try:
        candidate = absolute_url(text, base)
        parts = urlsplit(candidate)
        _ = parts.port
    except (TypeError, ValueError, UnicodeError):
        return None
    if parts.scheme not in ("http", "https") or parts.hostname is None:
        return None
    return candidate


def _rewritten(status: int, method, body):
    """The method and body the next hop carries: Chrome's own table."""
    upper = str(method or "GET").upper()
    if status == 303:
        return ("HEAD", None) if upper == "HEAD" else ("GET", None)
    if status in (301, 302) and upper == "POST":
        return "GET", None
    return upper, body


def next_hop(*, status, method, url, headers=None, body=None, location=None,
             hops: int = 0, mode: str = "cors", redirect: str = "follow"):
    """What the load path does with a response it just received.

    ``Deliver`` hands the response to the page as it arrived, ``Follow`` names the next
    request to make, and a redirect mode of ``error``, a ``Location`` that cannot be
    resolved or a chain past :data:`REDIRECT_LIMIT` raise :class:`RedirectFailure`.
    A navigation follows whatever its redirect mode says, because the mode is a fetch
    concept and a document load is not a fetch.
    """
    if not is_redirect(status):
        return DELIVERED
    if mode != "navigate":
        if redirect == "error":
            raise RedirectFailure(f"redirect mode 'error' refuses a {int(status)} response")
        if redirect == "manual":
            return OPAQUE
    text = location_text(location)
    if not text:
        return DELIVERED
    target = resolved_location(text, url)
    if target is None:
        raise BadLocation(text)
    if hops >= REDIRECT_LIMIT:
        raise TooManyRedirects()
    hop_method, hop_body = _rewritten(int(status), method, body)
    cross_origin = not same_origin(url, target)
    dropped = set(_BODY_HEADERS) if hop_body is None else set()
    if cross_origin:
        dropped |= _ACROSS_ORIGIN_DROPPED
    hop_headers = {name: value for name, value in (headers or {}).items()
                   if name.lower() not in dropped}
    return Follow(target, hop_method, hop_body, hop_headers, cross_origin)
