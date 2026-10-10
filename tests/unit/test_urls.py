"""Origin primitives and the urlencoded serializer, which need no JavaScript context."""
from pyv8c.foundation.urls import form_urlencoded, origin_tuple, quote_form


def test_origin_normalization_and_unique_opaque_origins():
    assert origin_tuple('HTTP://EXAMPLE.TEST:80/a?b#c') == ('http', 'example.test', 80)
    assert origin_tuple('https://example.test') == ('https', 'example.test', 443)
    assert origin_tuple('data:text/html,x') != origin_tuple('data:text/html,x')


def test_the_urlencoded_serializer_leaves_only_its_safe_set_alone():
    """Chrome sent ``a%7Eb*c%28d%29e_f-g.h%21i%27j`` for the same characters."""
    assert quote_form("a~b*c(d)e_f-g.h!i'j") == "a%7Eb*c%28d%29e_f-g.h%21i%27j"
    assert quote_form("hello world") == "hello+world"
    assert quote_form("\u4e2d") == "%E4%B8%AD"
    assert quote_form("100%") == "100%25"


def test_form_urlencoded_pairs_names_with_values():
    assert form_urlencoded([("a", "1"), ("b", "2 3")]) == "a=1&b=2+3"
    assert form_urlencoded([("a", "1"), ("a", "2")]) == "a=1&a=2"
    assert form_urlencoded([]) == ""
