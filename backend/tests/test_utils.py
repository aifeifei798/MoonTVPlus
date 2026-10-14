"""`moontv/utils.py` — the JS-builtin stand-ins.

These are the helpers every route leans on, so a wrong edge case here shows up
as a wrong wire body several modules later. The expectations are the values node
produces, not the values Python would.
"""

from __future__ import annotations

import math

from moontv.utils import (
    clean_html_tags,
    encode_uri_component,
    json_dump,
    js_min,
    js_number,
    js_parse_int,
    js_string_of,
    js_truthy,
    split_key,
    strip_none,
    url_search_params,
    url_search_value,
)


def test_strip_none_matches_json_stringify_dropping_undefined():
    assert strip_none({"a": 1, "b": None, "c": {"d": None, "e": 2}}) == {"a": 1, "c": {"e": 2}}
    # Array holes are not object keys, so they stay `None` — JS keeps `null` in
    # an array literal.
    assert strip_none([None, 1, None]) == [None, 1, None]
    assert json_dump(strip_none({"poster": None, "title": "x"})) == '{"title":"x"}'
    # Non-ASCII is kept literal, exactly like JSON.stringify.
    assert json_dump({"t": "世界"}) == '{"t":"世界"}'


def test_encode_uri_component_safe_set():
    assert encode_uri_component("a b/c?d=e&f") == "a%20b%2Fc%3Fd%3De%26f"
    assert encode_uri_component("!'()*-._~") == "!'()*-._~"
    assert encode_uri_component("@%") == "%40%25"
    assert encode_uri_component("世界") == "%E4%B8%96%E7%95%8C"
    assert encode_uri_component(None) == ""


def test_url_search_value_is_urlsearchparams_not_encodeuri():
    assert url_search_value("a b") == "a+b"
    assert url_search_value("a!b") == "a%21b"  # `!` survives encodeURIComponent, not URLSearchParams
    assert url_search_value("*-._") == "*-._"
    assert url_search_value(True) == "true"
    assert url_search_value(float("nan")) == "NaN"
    assert url_search_value(None) == "NaN"
    assert url_search_params([("ac", "videolist"), ("wd", "星球 大战")]) == "ac=videolist&wd=%E6%98%9F%E7%90%83+%E5%A4%A7%E6%88%98"


def test_js_parse_int():
    assert js_parse_int("12abc") == 12
    assert js_parse_int("1e3") == 1
    assert js_parse_int("0x10") == 0
    assert js_parse_int(" 7 ") == 7
    assert js_parse_int("-3") == -3
    assert js_parse_int("abc") is None
    assert js_parse_int("") is None
    assert js_parse_int(None) is None
    assert js_parse_int(12.9) == 12
    # The leading-whitespace class must be `*`, not `+`: `parseInt(" 7")` is 7.
    assert js_parse_int("\t7") == 7


def test_js_number_and_min():
    assert js_number("12") == 12.0
    assert js_number("") == 0
    assert js_number("abc") is None
    assert js_number(True) == 1
    assert js_number(float("nan")) is None
    # `js_number(x) or 0` is the port's `Number(x) || 0`; a Python NaN would be
    # truthy and leak through, so NaN has to come back as None.
    assert (js_number("abc") or 0) == 0
    assert js_min("3", 5) == 3
    assert js_min("abc", 5) is None
    assert js_min(None, 5) is None


def test_js_string_of():
    assert js_string_of(5.0) == "5"
    assert js_string_of(5.5) == "5.5"
    assert js_string_of(True) == "true"
    assert js_string_of(None) == "undefined"
    assert js_string_of({}) == "[object Object]"
    assert js_string_of([1, 2]) == "1,2"


def test_js_truthy():
    for value in (0, "", None, False, float("nan")):
        assert js_truthy(value) is False
    for value in ({}, [], "0", 1, True):
        assert js_truthy(value) is True


def test_clean_html_tags():
    assert clean_html_tags("<p>a</p><br>b") == "a\nb"
    assert clean_html_tags("  multiple   spaces  ") == "multiple spaces"
    assert clean_html_tags("&amp; &nbsp; &#39;") == "& \xa0 '"
    assert clean_html_tags("\n<p>x</p>\n") == "x"
    assert clean_html_tags("") == ""
    assert clean_html_tags(None) == ""


def test_split_key_takes_only_the_first_two_parts():
    assert split_key("IK+1") == ("IK", "1")
    assert split_key("a+b+c") == ("a", "b")
    assert split_key("only") == ("only", "")
    assert split_key("") == ("", "")


def test_now_ms_is_epoch_milliseconds():
    import time

    from moontv.utils import now_ms

    value = now_ms()
    assert isinstance(value, int)
    assert value <= math.floor(time.time() * 1000)
