"""Each collected fact reaches the page through the API that reports it.

No pump here: driving the task queue in this file perturbs the frame tests that run after it
(measured), so the facts that answer asynchronously are checked without a browser below.
"""
import pytest

pytest.importorskip("pyv8")

from pyv8c import Browser  # noqa: E402
from pyv8c.foundation.environment import BrowserEnvironment  # noqa: E402
from pyv8c.web.bom.accessories import _SPEECH_VOICES, _voice_entries  # noqa: E402

PAGE = "<!doctype html><body>environment</body>"


def test_each_collected_fact_reaches_the_page():
    """Overriding one key has to move the answer a page reads, or the key is decorative."""
    browser = Browser(environment={
        "viewport.outer_inset_width": 4,
        "viewport.outer_inset_height": 30,
        "media.constraints": ["width", "height"],
        "gpu.wgsl_features": ["subgroup_id"],
    }, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content(PAGE, url="https://environment.test/")
        assert page.evaluate("""(() => ({
            inset: [outerWidth - innerWidth, outerHeight - innerHeight],
            constraints: Object.keys(navigator.mediaDevices.getSupportedConstraints()),
            wgsl: navigator.gpu ? [navigator.gpu.wgslLanguageFeatures.size,
                                    navigator.gpu.wgslLanguageFeatures[0]] : null,
        }))()""") == {
            "inset": [4, 30],
            "constraints": ["width", "height"],
            "wgsl": [1, "subgroup_id"],
        }
    finally:
        browser.close()


def test_the_voices_are_the_collected_ones_and_the_table_without_a_profile():
    class _Realm:
        environment = BrowserEnvironment({
            "speech.voices": [{"name": "Test Voice", "lang": "en-GB", "local_service": True}]})

    class _Services:
        realm_services = _Realm()

    assert _voice_entries(_Services()) == (("Test Voice", "en-GB", True, False),)
    assert _voice_entries(None) == _SPEECH_VOICES
    assert _voice_entries(_Services()) != _SPEECH_VOICES, "the machine's voices are not this table"
