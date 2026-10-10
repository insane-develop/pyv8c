import pytest

pytest.importorskip("pyv8")

from pyv8c import Browser, BrowserEnvironment


def test_environment_defaults_and_mapping_overlay_are_immutable():
    default = BrowserEnvironment()
    assert default.user_agent.startswith("Mozilla/5.0 ")
    assert not default.user_agent.startswith("'")
    assert default.language == default.languages[0]
    assert default.languages
    assert default.viewport.width == 1904

    custom = default.overlay({"languages": ["zh-CN", "en"], "viewport.width": 390})
    assert custom.language == "zh-CN"
    assert custom.languages == ("zh-CN", "en")
    assert custom.viewport.width == 390
    assert custom.viewport.height == default.viewport.height
    assert custom.to_dict() == {"languages": ("zh-CN", "en"), "viewport.width": 390}
    with pytest.raises(AttributeError):
        custom.viewport = None
    with pytest.raises(ValueError):
        BrowserEnvironment({"geolocation.permission": "sometimes"})


def test_environment_drives_navigator_viewport_and_screen():
    environment = BrowserEnvironment({
        "user_agent": "Example UA",
        "platform": "Linux x86_64",
        "languages": ["zh-CN", "en"],
        "hardware_concurrency": 12,
        "device_memory": 4,
        "max_touch_points": 5,
        "viewport.width": 390,
        "viewport.height": 844,
        "viewport.device_scale_factor": 3,
        "screen.width": 1080,
        "screen.height": 2400,
        "screen.avail_width": 1080,
        "screen.avail_height": 2300,
    })
    browser = Browser(environment=environment)
    try:
        page = browser.new_context().new_page()
        # The insets are whatever the profile collected: outer is inner plus those.
        assert page.evaluate("[navigator.userAgent, navigator.platform, navigator.language, navigator.languages, navigator.hardwareConcurrency, navigator.deviceMemory, navigator.maxTouchPoints, innerWidth, innerHeight, outerWidth, outerHeight, devicePixelRatio, screen.width, screen.availHeight]") == [
            "Example UA", "Linux x86_64", "zh-CN", ["zh-CN", "en"], 12, 4, 5,
            390, 844,
            390 + environment.viewport.outer_inset_width,
            844 + environment.viewport.outer_inset_height,
            3, 1080, 2300,
        ]
    finally:
        browser.close()


def test_environment_drives_the_client_hints():
    browser = Browser(environment={
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                      " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "client_hints.platform_version": "6.5.0",
    })
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://secure.test/")
        assert page.evaluate("""(() => {
            const data = navigator.userAgentData;
            return [data.platform, data.mobile,
                    data.brands.map(brand => brand.brand + '/' + brand.version)];
        })()""") == ["Linux", False, ["Chromium/120", "Google Chrome/120", "Not A(Brand/99"]]

        # The hints are derived from the user agent, and an explicit member wins.
        page.evaluate("""(() => {
            window.__hints = 'pending';
            navigator.userAgentData
                .getHighEntropyValues(['architecture', 'bitness', 'platformVersion',
                                       'uaFullVersion', 'formFactors'])
                .then(value => window.__hints = [value.architecture, value.bitness,
                                                 value.platformVersion, value.uaFullVersion,
                                                 value.formFactors],
                      error => window.__hints = error.name);
        })()""")
        page.run_until_idle(timeout=1)
        # The full version is not derivable from a user agent, so it stays the
        # profile's, while the platform the UA names is worked out again.
        assert page.evaluate("window.__hints") == [
            "x86", "64", "6.5.0",
            BrowserEnvironment().client_hints.full_version_list[0].version,
            ["Desktop"],
        ]
    finally:
        browser.close()


def test_page_environment_override_and_iframe_inheritance():
    browser = Browser(
        environment={"user_agent": "Browser UA"},
        resource_loader=lambda url, request: {"body": "<body>frame</body>", "url": url},
    )
    try:
        context = browser.new_context()
        first = context.new_page(environment={"user_agent": "Page UA", "viewport.width": 800})
        second = context.new_page()
        assert first.evaluate("[navigator.userAgent, innerWidth]") == ["Page UA", 800]
        assert second.evaluate("[navigator.userAgent, innerWidth]") == ["Browser UA", 1904]
        iframe = first.document.fn_createElement("iframe")
        iframe.set_src("https://example.test/frame")
        child = first.resources.load_iframe(iframe)
        assert child.window.navigator.get_userAgent() == "Page UA"
    finally:
        browser.close()


def test_geolocation_is_async_and_clear_watch_cancels_pending_callback():
    browser = Browser(environment={
        "geolocation.permission": "granted",
        "geolocation.latitude": 31.2,
        "geolocation.longitude": 121.5,
        "geolocation.accuracy": 5,
    })
    try:
        page = browser.new_context().new_page()
        # The position callback is delivered on the page task queue, never
        # inline, so it is still pending when the requesting script ends.
        assert page.evaluate(
            "navigator.geolocation.getCurrentPosition(p => globalThis.geo = [p.coords.latitude, p.coords.longitude, p.coords.accuracy]); typeof geo"
        ) == "undefined"
        page.run_until_idle()
        assert page.evaluate("geo") == [31.2, 121.5, 5]

        assert page.evaluate("(() => { globalThis.cancelled = 0; const id = navigator.geolocation.watchPosition(() => cancelled++); navigator.geolocation.clearWatch(id); return id; })()") == 1
        page.run_until_idle()
        assert page.evaluate("cancelled") == 0
    finally:
        browser.close()


@pytest.mark.parametrize(
    ("geolocation", "expected"),
    [
        ({"geolocation.permission": "denied"}, [1, "User denied Geolocation"]),
        ({"geolocation.permission": "granted"}, [2, "Unable to determine the position"]),
    ],
)
def test_geolocation_reports_permission_and_position_errors(geolocation, expected):
    browser = Browser(environment=geolocation)
    try:
        page = browser.new_context().new_page()
        page.evaluate("navigator.geolocation.getCurrentPosition(() => globalThis.geoError = ['success'], error => globalThis.geoError = [error.code, error.message])")
        page.run_until_idle()
        assert page.evaluate("geoError") == expected
    finally:
        browser.close()


def test_resource_loader_gets_environment_headers_and_explicit_headers_win():
    calls = []

    def loader(url, request):
        calls.append(request)
        return {"body": "ok", "url": url}

    browser = Browser(resource_loader=loader, environment={"user_agent": "Env UA", "languages": ["fr-FR", "en"]})
    try:
        page = browser.new_context().new_page("https://example.test/")
        calls.clear()
        page.resources.load_resource("/one")
        page.resources.load_resource("/two", {"headers": {"user-agent": "Explicit UA", "ACCEPT-LANGUAGE": "de"}})
        assert calls[0]["headers"]["User-Agent"] == "Env UA"
        assert calls[0]["headers"]["Accept-Language"] == "fr-FR,en;q=0.9"
        assert calls[0]["headers"]["Accept"] == "*/*"
        assert calls[0]["headers"]["Sec-Fetch-Site"] == "same-origin"
        assert calls[0]["headers"]["Referer"] == "https://example.test/"
        assert calls[1]["headers"]["user-agent"] == "Explicit UA"
        assert calls[1]["headers"]["ACCEPT-LANGUAGE"] == "de"
        assert calls[1]["headers"]["Accept"] == "*/*"
        assert calls[1]["headers"]["Sec-Fetch-Dest"] == "empty"
        assert "User-Agent" not in calls[1]["headers"]
    finally:
        browser.close()


def test_resource_interceptor_filters_images_and_styles_and_cache_reuses_loader(tmp_path):
    calls = []

    def loader(url, request):
        calls.append((url, request.get("resource_type")))
        return {"body": "cached", "url": url, "headers": {"content-type": "text/javascript"}}

    def interceptor(url, request):
        if request.get("resource_type") in {"image", "style"}:
            return False

    browser = Browser(resource_loader=loader, resource_interceptor=interceptor, cache_dir=tmp_path / "cache")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body><img src='/a.png'><link rel='stylesheet' href='/a.css'></body>", url="http://example.test/")
        page.evaluate("fetch('/app.js').then(r => r.text()).then(v => window.__one=v); fetch('/app.js').then(r => r.text()).then(v => window.__two=v)")
        page.run_until_idle(timeout=2)
        assert page.evaluate("[__one, __two]") == ["cached", "cached"]
        assert [kind for _url, kind in calls].count("fetch") == 1
        assert all(kind not in {"image", "style"} for _url, kind in calls)
    finally:
        browser.close()


def test_every_request_a_page_makes_names_its_resource_type():
    """A caller filters on ``resource_type``, so each path names itself: the element
    ones, the API ones (``fetch``, ``xhr``), module imports and worker sources."""
    kinds = {}
    body = """<body>
<img src="/a.png"><link rel="stylesheet" href="/app.css">
<iframe src="/frame.html"></iframe>
<script src="/classic.js"></script>
<script type="module">import '/module.js';</script>
<script>
  new Worker('/worker.js');
  fetch('/page-fetch');
  var xhr = new XMLHttpRequest(); xhr.open('GET', '/page-xhr', true); xhr.send();
</script>
</body>"""

    def loader(url, request):
        kinds.setdefault(request.get("resource_type"), []).append(str(url))
        name = str(url).rsplit("/", 1)[-1]
        if name.endswith(".css"):
            return {"body": "body{}", "url": url, "headers": {"content-type": "text/css"}}
        if name.endswith(".js"):
            return {"body": "0;", "url": url,
                    "headers": {"content-type": "text/javascript"}}
        if name.endswith(".html"):
            return {"body": "<body>frame</body>", "url": url,
                    "headers": {"content-type": "text/html"}}
        return {"body": body, "url": url, "headers": {"content-type": "text/html"}}

    browser = Browser(resource_loader=loader, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.goto("http://example.test/")
        page.run_until_idle(timeout=3)
        page.net.fetch("/host-fetch")
        page.net.xhr("/host-xhr")
        page.run_until_idle(timeout=3)
    finally:
        browser.close()

    assert None not in kinds
    assert {"document", "script", "image", "style", "iframe", "fetch", "xhr"} <= set(kinds)


def test_resource_cache_persists_across_browser_restart(tmp_path, monkeypatch):
    cache_dir = tmp_path / "resource-cache"
    first_calls = []

    def first_loader(url, request):
        first_calls.append(url)
        return {"body": "persisted-js", "url": url, "headers": {"content-type": "text/javascript"}}

    browser = Browser(resource_loader=first_loader, cache_dir=cache_dir)
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="http://cache.test/")
        assert page.resources.load_resource("/app.js").body_text() == "persisted-js"
        assert first_calls == ["http://cache.test/app.js"]
        assert len(list((cache_dir / "cache.test").glob("*.json"))) == 1
    finally:
        browser.close()

    def unavailable_loader(url, request):
        raise AssertionError("persistent cache was not used")

    messages = []
    monkeypatch.setattr(
        "pyv8c.platform.resource_loading.logger.debug",
        lambda message, *values: messages.append((message, *values)),
    )
    browser = Browser(resource_loader=unavailable_loader, cache_dir=cache_dir)
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="http://cache.test/")
        assert page.resources.load_resource("/app.js").body_text() == "persisted-js"
        assert ("resource cache hit: {}", "http://cache.test/app.js") in messages
    finally:
        browser.close()


def test_worker_inherits_page_environment_snapshot():
    source = "self.onmessage = () => self.postMessage([navigator.userAgent, navigator.language, navigator.maxTouchPoints]);"
    browser = Browser(
        resource_loader=lambda url, request: {"body": source, "url": url},
        environment={"user_agent": "Browser UA", "languages": ["en-US"], "max_touch_points": 0},
    )
    try:
        page = browser.new_context().new_page(environment={"user_agent": "Worker UA", "languages": ["ja-JP"], "max_touch_points": 2})
        page.evaluate("const worker = new Worker('https://example.test/worker.js'); worker.onmessage = event => globalThis.workerEnvironment = event.data; worker.postMessage(null);")
        page.run_until_idle(2)
        assert page.evaluate("workerEnvironment") == ["Worker UA", "ja-JP", 2]
    finally:
        browser.close()


def test_worker_navigator_does_not_expose_window_only_surfaces():
    source = "self.postMessage([typeof navigator.plugins, typeof navigator.mimeTypes, typeof navigator.geolocation]);"
    browser = Browser(resource_loader=lambda url, request: {"body": source, "url": url})
    try:
        page = browser.new_context().new_page()
        page.evaluate("const worker = new Worker('https://example.test/worker.js'); worker.onmessage = event => globalThis.workerSurfaces = event.data;")
        page.run_until_idle(1)
        assert page.evaluate("workerSurfaces") == ["undefined", "undefined", "undefined"]
    finally:
        browser.close()


def test_fetch_and_xhr_receive_environment_headers():
    calls = []

    def loader(url, request):
        calls.append((url, request["headers"]))
        return {"body": "ok", "url": url, "headers": {"access-control-allow-origin": "*"}}

    browser = Browser(resource_loader=loader, environment={"user_agent": "Async UA", "languages": ["ko-KR", "en"]})
    try:
        page = browser.new_context().new_page("https://example.test/")
        calls.clear()
        page.evaluate("fetch('/fetch').then(() => globalThis.fetchDone = true)")
        page.evaluate("const xhr = new XMLHttpRequest(); xhr.open('GET', '/xhr'); xhr.onload = () => globalThis.xhrDone = true; xhr.send()")
        page.run_until_idle(1)
        assert page.evaluate("[fetchDone, xhrDone]") == [True, True]
        # fetch and XHR are one destination on the wire: the browser set, identical.
        assert [headers for _, headers in calls][0] == [headers for _, headers in calls][1]
        for _url, headers in calls:
            assert headers["User-Agent"] == "Async UA"
            assert headers["Accept-Language"] == "ko-KR,en;q=0.9"
            assert headers["Sec-Fetch-Mode"] == "cors"
            assert headers["Sec-Fetch-Dest"] == "empty"
            assert headers["Referer"] == "https://example.test/"
    finally:
        browser.close()


def test_fetch_blob_url_uses_context_storage_async():
    browser = Browser(environment={"user_agent": "Blob UA"})
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        assert page.evaluate("(() => { const u = URL.createObjectURL(new Blob(['blob-body'])); fetch(u).then(r => r.text()).then(v => globalThis.blobFetch = v); return u; })()").startswith("blob:")
        page.run_until_idle(1)
        assert page.evaluate("blobFetch") == "blob-body"
    finally:
        browser.close()
