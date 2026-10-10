"""The header table held to what Chrome 153 put on the wire; every expectation here
is a transcription of a real browser's request (``docs/request-headers.md``)."""

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytestmark = pytest.mark.native

from pyv8c.foundation.environment import BrowserEnvironment
from pyv8c.platform.request_headers import (
    ACCEPT_ENCODING,
    NAVIGATION_ACCEPT,
    accept_language,
    browser_headers,
    fetch_mode,
    include_origin,
    referer_for,
    same_site,
    site_relation,
)

PAGE_URL = "http://127.0.0.1:8080/page"
CROSS_URL = "http://127.0.0.1:8081/data"
NAVIGATION_ORDER = (
    "Connection", "Cache-Control", "sec-ch-ua", "sec-ch-ua-mobile",
    "sec-ch-ua-platform", "Upgrade-Insecure-Requests", "Content-Type",
    "User-Agent", "Origin", "Accept", "Sec-Fetch-Site", "Sec-Fetch-Mode",
    "Sec-Fetch-User", "Sec-Fetch-Dest", "Referer", "Accept-Encoding",
    "Accept-Language",
)
#: The headers a plain GET navigation does not carry: Chrome places them here
#: when a form submission fills them in.
NAVIGATION_ONLY_WHEN_POSTED = ("Cache-Control", "Content-Type", "Origin")
RESOURCE_ORDER = (
    "Connection", "sec-ch-ua-platform", "User-Agent", "sec-ch-ua",
    "sec-ch-ua-mobile", "Accept", "Origin", "Sec-Fetch-Site", "Sec-Fetch-Mode",
    "Sec-Fetch-Dest", "Referer", "Accept-Encoding", "Accept-Language",
)
MEDIA_ORDER = (
    "Connection", "sec-ch-ua-platform", "Accept-Encoding", "User-Agent",
    "sec-ch-ua", "sec-ch-ua-mobile", "Accept", "Origin", "Sec-Fetch-Site",
    "Sec-Fetch-Mode", "Sec-Fetch-Dest", "Referer", "Accept-Language", "Range",
)

CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
CHROME_HINTS = {
    "sec-ch-ua": '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
SCRIPT_ACCEPT = "*/*"
STYLE_ACCEPT = "text/css,*/*;q=0.1"
IMAGE_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"


def environment():
    return BrowserEnvironment()


def headers(**kwargs):
    kwargs.setdefault("url", PAGE_URL)
    kwargs.setdefault("current_url", PAGE_URL)
    return browser_headers(environment(), **kwargs)


def order_of(headers):
    return tuple(name for name in headers if headers[name])


def expected(order, dropped=()):
    """A template with the headers this scene does not send left out."""
    return tuple(name for name in order if name not in dropped)


def test_navigation_order_and_values_match_the_browser():
    sent = headers(destination="document", browser_initiated=True)
    assert order_of(sent) == expected(NAVIGATION_ORDER, dropped=("Referer", *NAVIGATION_ONLY_WHEN_POSTED))
    assert sent["Accept"] == NAVIGATION_ACCEPT
    assert sent["User-Agent"] == CHROME_UA
    assert sent["Upgrade-Insecure-Requests"] == "1"
    assert sent["Sec-Fetch-Site"] == "none"
    assert sent["Sec-Fetch-Mode"] == "navigate"
    assert sent["Sec-Fetch-User"] == "?1"
    assert sent["Sec-Fetch-Dest"] == "document"
    assert sent["Accept-Encoding"] == ACCEPT_ENCODING
    assert sent["Accept-Language"] == "zh-CN,zh;q=0.9"
    assert "Referer" not in sent
    assert {name: sent[name] for name in CHROME_HINTS} == CHROME_HINTS


def test_frame_navigation_is_the_navigation_order_without_user_activation():
    sent = headers(destination="iframe")
    assert order_of(sent) == expected(
        NAVIGATION_ORDER, dropped=("Sec-Fetch-User", *NAVIGATION_ONLY_WHEN_POSTED))
    assert sent["Sec-Fetch-Dest"] == "iframe"
    assert sent["Sec-Fetch-Site"] == "same-origin"
    assert sent["Referer"] == PAGE_URL
    assert "Sec-Fetch-User" not in sent


def test_a_form_submission_is_a_navigation_that_carries_a_body():
    """Chrome 153: ``Content-Type`` before ``User-Agent``, ``Origin`` after it,
    and ``Cache-Control: max-age=0`` right after ``Connection``."""
    sent = headers(destination="document", method="POST", user_activated=True,
                   content_type="application/x-www-form-urlencoded")
    assert order_of(sent) == NAVIGATION_ORDER
    assert sent["Content-Type"] == "application/x-www-form-urlencoded"
    assert sent["Cache-Control"] == "max-age=0"
    assert sent["Origin"] == "http://127.0.0.1:8080"
    assert sent["Sec-Fetch-User"] == "?1"
    assert sent["Referer"] == PAGE_URL


def test_a_script_submission_is_the_same_request_without_user_activation():
    sent = headers(destination="document", method="POST",
                   content_type="application/x-www-form-urlencoded")
    assert "Sec-Fetch-User" not in sent
    assert sent["Origin"] == "http://127.0.0.1:8080"
    assert sent["Cache-Control"] == "max-age=0"


def test_a_safe_navigation_sends_no_origin_and_no_cache_control():
    sent = headers(destination="document", method="GET", user_activated=True)
    assert "Origin" not in sent
    assert "Cache-Control" not in sent
    assert sent["Sec-Fetch-User"] == "?1"


def test_subresources_use_the_renderer_order():
    for destination, accept in (
        ("script", SCRIPT_ACCEPT), ("style", STYLE_ACCEPT), ("image", IMAGE_ACCEPT),
    ):
        sent = headers(destination=destination)
        assert order_of(sent) == expected(RESOURCE_ORDER, dropped=("Origin",)), destination
        assert sent["Accept"] == accept, destination
        assert sent["Sec-Fetch-Mode"] == "no-cors", destination
        assert sent["Sec-Fetch-Site"] == "same-origin", destination
        assert sent["Referer"] == PAGE_URL, destination
        assert "Origin" not in sent, destination


def test_media_asks_for_its_bytes_by_range_and_uncompressed():
    for destination in ("audio", "video"):
        sent = headers(destination=destination)
        assert order_of(sent) == expected(MEDIA_ORDER, dropped=("Origin",)), destination
        assert sent["Accept-Encoding"] == "identity;q=1, *;q=0", destination
        assert sent["Range"] == "bytes=0-", destination
        assert sent["Accept"] == SCRIPT_ACCEPT, destination
        assert sent["Sec-Fetch-Mode"] == "no-cors", destination


def test_a_caller_header_is_spliced_where_chrome_puts_it():
    sent = headers(destination="empty", method="POST", mode="cors",
                   extra={"Content-Type": "application/json"})
    spliced = list(RESOURCE_ORDER)
    spliced.insert(spliced.index("sec-ch-ua") + 1, "Content-Type")
    assert order_of(sent) == tuple(spliced)
    assert sent["Content-Type"] == "application/json"


def test_a_caller_header_replaces_a_default_of_the_same_name():
    sent = headers(destination="script", extra={"user-agent": "Explicit UA"})
    assert sent["user-agent"] == "Explicit UA"
    assert "User-Agent" not in sent


def test_origin_is_sent_for_cross_origin_or_unsafe_methods_only():
    assert "Origin" not in headers(destination="empty")
    assert headers(destination="empty", method="POST")["Origin"] == "http://127.0.0.1:8080"
    cross = headers(destination="empty", url=CROSS_URL)
    assert cross["Origin"] == "http://127.0.0.1:8080"
    assert cross["Sec-Fetch-Site"] == "same-site"
    assert cross["Referer"] == "http://127.0.0.1:8080/"
    # A form POST navigates instead of fetching, and still sends the origin.
    assert headers(destination="document", method="POST")["Origin"] == "http://127.0.0.1:8080"
    assert "Origin" not in headers(destination="document")


def test_https_omits_connection_because_it_negotiates_http2():
    assert "Connection" not in headers(destination="document", browser_initiated=True,
                                      http_version=2)


def test_xhr_and_fetch_take_the_empty_destination_in_cors_mode():
    assert fetch_mode("empty") == "cors"
    assert fetch_mode("empty", "same-origin") == "cors"
    assert fetch_mode("script") == "no-cors"
    assert fetch_mode("script", "cors") == "cors"
    assert fetch_mode("document") == "navigate"
    assert include_origin(PAGE_URL, PAGE_URL, "cors", "GET") is False
    assert include_origin(PAGE_URL, PAGE_URL, "cors", "POST") is True
    assert include_origin(PAGE_URL, PAGE_URL, "navigate", "GET") is False
    assert include_origin(PAGE_URL, PAGE_URL, "navigate", "POST") is True


def test_accept_language_follows_the_browsers_q_value_rule():
    assert accept_language(("zh-CN", "zh")) == "zh-CN,zh;q=0.9"
    assert accept_language(("fr-FR", "en")) == "fr-FR,en;q=0.9"
    assert accept_language(("de", "en", "fr", "es")) == "de,en;q=0.9,fr;q=0.8,es;q=0.7"
    # The tenth step never goes below 0.1, and the first entry carries no q at all.
    assert accept_language(("a", "b", "c", "d", "e")) == "a,b;q=0.9,c;q=0.8,d;q=0.7,e;q=0.6"
    assert accept_language(()) == ""


def test_referer_and_site_relations_come_from_the_initiator():
    assert referer_for(PAGE_URL, PAGE_URL) == PAGE_URL
    assert referer_for(CROSS_URL, PAGE_URL) == "http://127.0.0.1:8080/"
    assert referer_for(PAGE_URL, "") == ""
    assert site_relation(PAGE_URL, PAGE_URL) == "same-origin"
    assert site_relation(CROSS_URL, PAGE_URL) == "same-site"
    assert site_relation("https://127.0.0.1:8080/x", PAGE_URL) == "cross-site"
    assert same_site("http://a.example.com/", "http://b.example.com/") is True
    assert same_site("http://example.com/", "http://other.test/") is False
