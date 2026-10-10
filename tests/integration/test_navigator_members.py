"""The Navigator sub-objects: when they exist, and what they answer."""

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytestmark = pytest.mark.native

from pyv8c import Browser

MEMBERS = ("mediaDevices", "permissions", "mediaSession", "serviceWorker", "credentials",
           "bluetooth", "storage")


def _settled(page, expression, *, name="__settled"):
    """What *expression*'s promise settles as: a value, or an error's name."""
    page.evaluate(
        "(() => { Promise.resolve(" + expression + ").then("
        f"(value) => {{ window.{name} = ['value', value]; }},"
        f"(error) => {{ window.{name} = ['error', error.name, error.message]; }}); }})()")
    page.run_until_idle(timeout=2)
    return page.evaluate(f"window.{name}")


def test_each_member_answers_by_secure_context():
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://secure.test/")
        assert page.evaluate("[" + ",".join(
            f"typeof navigator.{name}" for name in MEMBERS) + ", navigator.javaEnabled()]") == [
            "object", "object", "object", "object", "object", "object", "object", False]
        page.set_content("<body></body>", url="http://insecure.test/")
        assert page.evaluate("[" + ",".join(
            f"typeof navigator.{name}" for name in MEMBERS) + ", navigator.javaEnabled()]") == [
            "undefined", "object", "object", "undefined", "undefined", "undefined", "undefined",
            False]
    finally:
        browser.close()


def test_each_member_is_the_same_object_twice():
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://secure.test/")
        assert page.evaluate("[" + ",".join(
            f"navigator.{name} === navigator.{name}" for name in MEMBERS) + "]") == [True] * 7
    finally:
        browser.close()


def test_a_worker_gets_only_the_members_chrome_gives_it():
    source = ("self.postMessage([typeof navigator.permissions, typeof navigator.storage,"
              " typeof navigator.serviceWorker, typeof navigator.mediaDevices,"
              " typeof navigator.mediaSession, typeof navigator.credentials,"
              " typeof navigator.bluetooth, typeof navigator.javaEnabled]);")
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://secure.test/")
        page.evaluate("""(() => {
            const source = new Blob([%s], {type: 'text/javascript'});
            const worker = new Worker(URL.createObjectURL(source));
            worker.onmessage = (event) => { window.__workerMembers = event.data; };
        })()""" % __import__("json").dumps(source))
        page.run_until_idle(timeout=2)
        assert page.evaluate("__workerMembers") == [
            "object", "object", "undefined", "undefined", "undefined", "undefined", "undefined",
            # Chrome's WorkerNavigator has no javaEnabled at all; a member declared
            # on the shared prototype cannot be taken away from it.
            "function"]
    finally:
        browser.close()


def test_the_environment_answers_bluetooth_and_the_geolocation_permission():
    browser = Browser(environment={"bluetooth.available": False,
                                   "geolocation.permission": "granted"},
                      stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://secure.test/")
        assert _settled(page, "navigator.bluetooth.getAvailability()") == ["value", False]
        assert _settled(page, "navigator.permissions.query({name: 'geolocation'})"
                              ".then((status) => [status.name, status.state])") == [
            "value", ["geolocation", "granted"]]
    finally:
        browser.close()


def test_the_calls_that_need_a_chooser_or_a_registration_refuse():
    def loader(url, _request):
        return {"body": "self.onmessage = () => {};", "url": url,
                "headers": {"content-type": "text/javascript"}}

    browser = Browser(resource_loader=loader, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://secure.test/")
        assert _settled(page, "navigator.mediaDevices.getDisplayMedia({video: true})") == [
            "error", "NotAllowedError", "Permission denied"]
        assert _settled(page, "navigator.bluetooth.requestDevice({acceptAllDevices: true})") == [
            "error", "NotFoundError", "User cancelled the requestDevice() chooser."]
        assert _settled(page, "navigator.storage.getDirectory()") == [
            "error", "NotSupportedError", "The origin private file system is not implemented here."]
        assert _settled(page, "navigator.serviceWorker.register('/worker.js')") == [
            "error", "NotSupportedError", "Service workers are not implemented here."]
        assert _settled(page, "navigator.serviceWorker.register('http://other.test/sw.js')") == [
            "error", "SecurityError",
            "Failed to register a ServiceWorker: The origin of the provided scriptURL "
            "('http://other.test') does not match the current origin ('https://secure.test')."]
    finally:
        browser.close()


def test_a_registration_whose_script_cannot_be_fetched_rejects():
    browser = Browser(real_network=False, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://secure.test/")
        settled = _settled(page, "navigator.serviceWorker.register('/sw.js')")
        assert settled[0] == "error"
        assert settled[1] == "TypeError"
    finally:
        browser.close()
