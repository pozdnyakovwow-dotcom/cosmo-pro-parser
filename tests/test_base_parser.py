import pytest

from parsers.base import BaseDoctorParser, SourceBlockedError


def test_base_parser_detects_antibot_html():
    parser = BaseDoctorParser()
    with pytest.raises(SourceBlockedError):
        parser._raise_for_bad_response(
            200,
            '<script src="//servicepipe.ru/loaders/default.js"></script>',
            "https://example.com",
        )


def test_base_parser_prefers_browser_for_flagged_sources():
    parser = BaseDoctorParser(
        browser_enabled=True,
        browser_config={"force_for_blocked_sources": True},
        use_browser_fallback=True,
    )
    assert parser._should_prefer_browser() is True


def test_base_parser_browser_html_ready_requires_non_blocked_h1():
    parser = BaseDoctorParser(manual_browser_assist=True)
    assert parser._browser_html_is_ready("<html><body><h1>Doctor</h1></body></html>") is True
    assert (
        parser._browser_html_is_ready(
            '<html><body><script src="//servicepipe.ru/loaders/default.js"></script></body></html>'
        )
        is False
    )
