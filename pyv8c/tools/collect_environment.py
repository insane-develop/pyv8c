"""Read a real browser's environment and window surface into one profile.

The collector starts the locally installed Chrome with a debugging port, reads every
key a browser reports from a local secure origin, and reads what that browser leaves out
of ``window`` and of each interface's prototype from the same session.  Nothing is
downloaded and nothing is guessed: a key that cannot be read fails the run, so a written
profile is whole.  Headless runs report ``HeadlessChrome`` in the user agent; use
``headed=True`` for the profile a normal visit presents.

Usage::
    python tools/collect_environment.py                     # writes the packaged profile
    python tools/collect_environment.py --out -             # to standard output
    python tools/collect_environment.py --headed --window-size 1920,1080
    python tools/collect_environment.py --accept-lang en-US,en
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..engine.idl import ArrayProto, Idl
from ..foundation.environment import COLLECTED_KEYS, SCHEMA_VERSION, BrowserEnvironment, json_value
from ..web.bom.navigator import PERMISSION_NAMES
from ..web.dom.media_source import WEBRTC_CONTENT_TYPES, WEBRTC_VIDEO_FAMILIES, probe_candidates

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
#: Where a run writes unless told otherwise.
DEFAULT_OUTPUT = PACKAGE_ROOT / "data" / "chrome-environment.json"


#: The same variable the reference corpus reads, so one setting points both at Chrome.
CHROME_ENV = "PYV8C_CHROME"
CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


#: One expression, so every value comes from the same turn of the same document.
_COLLECT_TEMPLATE = """
(async () => {
    const finite = value => Number.isFinite(value) ? value : String(value);
    const hints = navigator.userAgentData;
    const high = await hints.getHighEntropyValues(
        ['architecture', 'bitness', 'formFactors', 'fullVersionList', 'model',
         'platformVersion', 'uaFullVersion', 'wow64']);
    const battery = await navigator.getBattery();
    const estimate = await navigator.storage.estimate();
    const connection = navigator.connection;
    const layout = await navigator.keyboard.getLayoutMap();
    const layoutEntries = {};
    layout.forEach((value, key) => { layoutEntries[key] = value; });
    // Two bounded facts about media: which codecs this browser decodes, and which in hardware.
    const CANDIDATES = __MEDIA_CANDIDATES__;
    const WEBRTC_TYPES = __MEDIA_WEBRTC_TYPES__;
    const WEBRTC_FAMILIES = __MEDIA_WEBRTC_FAMILIES__;
    const mediaContainers = {};
    const mediaSelfContained = [];
    const mediaHardware = [];
    for (const container of Object.keys(CANDIDATES)) {
        const prefixes = [];
        for (const probe of CANDIDATES[container]) {
            const supported = MediaSource.isTypeSupported(probe);
            const at = probe.indexOf('codecs="');
            if (at < 0) {
                if (supported && mediaSelfContained.indexOf(container) < 0) {
                    mediaSelfContained.push(container);
                }
                continue;
            }
            if (!supported) continue;
            const codec = probe.slice(at + 8).replace(/".*$/, '').toLowerCase();
            const prefix = codec.split('.')[0];
            if (!prefix) continue;
            if (prefixes.indexOf(prefix) < 0) prefixes.push(prefix);
            const video = probe.indexOf('video/') === 0;
            const config = {type: 'file'};
            config[video ? 'video' : 'audio'] = video
                ? {contentType: probe, width: 640, height: 360, bitrate: 300000, framerate: 30}
                : {contentType: probe, channels: 2, samplerate: 48000, bitrate: 128000};
            const info = await navigator.mediaCapabilities.decodingInfo(config);
            if (info.powerEfficient && mediaHardware.indexOf(prefix) < 0) {
                mediaHardware.push(prefix);
            }
        }
        mediaContainers[container] = prefixes;
    }
    // The WebRTC path takes its own content types (``video/VP8``); both directions answer.
    const mediaWebrtc = {decode: [], encode: []};
    const mediaEncodeHardware = [];
    for (const type of WEBRTC_TYPES) {
        const video = type.indexOf('video/') === 0;
        for (const [method, direction, hardware] of
             [['decodingInfo', 'decode', null], ['encodingInfo', 'encode', mediaEncodeHardware]]) {
            const config = {type: 'webrtc'};
            config[video ? 'video' : 'audio'] = video
                ? {contentType: type, width: 640, height: 360, bitrate: 300000, framerate: 30}
                : {contentType: type, channels: 2, samplerate: 48000, bitrate: 128000};
            let info;
            try { info = await navigator.mediaCapabilities[method](config); }
            catch (error) { continue; }
            if (!info.supported) continue;
            mediaWebrtc[direction].push(type);
            const family = WEBRTC_FAMILIES[type.split('/')[1]];
            if (hardware !== null && info.powerEfficient && family && hardware.indexOf(family) < 0) {
                hardware.push(family);
            }
        }
    }
    // Capture devices by kind: ids and labels stay empty until a page is granted one.
    const mediaDevices = (await navigator.mediaDevices.enumerateDevices())
        .map(device => device.kind);
    const supportedConstraints = typeof navigator.mediaDevices.getSupportedConstraints === 'function'
        ? Object.keys(navigator.mediaDevices.getSupportedConstraints()) : [];
    // A name is one this build answers for: an unknown one makes query() reject.
    const permissionNames = [];
    const permissionGranted = [];
    const permissionSpecNames = {};
    for (const name of __PERMISSION_NAMES__) {
        try {
            const descriptor = name === 'push' ? {name, userVisibleOnly: true} : {name};
            const status = await navigator.permissions.query(descriptor);
            permissionNames.push(name);
            if (status.state === 'granted') permissionGranted.push(name);
            permissionSpecNames[name] = status.name;
        } catch (error) {}
    }
    // The voice list is empty until the synthesiser service answers, which is where
    // ``voiceschanged`` fires; a page reads the same list after that event.
    const speechVoices = await (async () => {
        if (typeof speechSynthesis === 'undefined') return [];
        const read = () => speechSynthesis.getVoices().map(voice => ({
            name: voice.name, lang: voice.lang,
            local_service: voice.localService, default: voice.default}));
        const first = read();
        if (first.length) return first;
        await new Promise(done => {
            const finish = () => {
                clearTimeout(timer);
                speechSynthesis.removeEventListener('voiceschanged', finish);
                done();
            };
            const timer = setTimeout(finish, 1500);
            speechSynthesis.addEventListener('voiceschanged', finish);
        });
        return read();
    })();
    const wgslFeatures = navigator.gpu && navigator.gpu.wgslLanguageFeatures
        ? Array.from(navigator.gpu.wgslLanguageFeatures) : [];
    const brands = list => list.map(entry => ({brand: entry.brand, version: entry.version}));
    const gl = document.createElement('canvas').getContext('webgl');
    const rendererInfo = gl === null ? null : gl.getExtension('WEBGL_debug_renderer_info');
    return JSON.stringify({
        'user_agent': navigator.userAgent,
        'app_version': navigator.appVersion,
        'vendor': navigator.vendor,
        'platform': navigator.platform,
        'language': navigator.language,
        'languages': Array.from(navigator.languages),
        'on_line': navigator.onLine,
        'hardware_concurrency': navigator.hardwareConcurrency,
        'device_memory': navigator.deviceMemory,
        'max_touch_points': navigator.maxTouchPoints,
        'webdriver': navigator.webdriver,
        'timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
        'viewport.width': innerWidth,
        'viewport.height': innerHeight,
        'viewport.device_scale_factor': devicePixelRatio,
        'viewport.outer_inset_width': outerWidth - innerWidth,
        'viewport.outer_inset_height': outerHeight - innerHeight,
        'screen.width': screen.width,
        'screen.height': screen.height,
        'screen.avail_width': screen.availWidth,
        'screen.avail_height': screen.availHeight,
        'screen.color_depth': screen.colorDepth,
        'screen.pixel_depth': screen.pixelDepth,
        'connection.effective_type': connection.effectiveType,
        'connection.rtt': connection.rtt,
        'connection.downlink': connection.downlink,
        'connection.save_data': connection.saveData,
        'battery.charging': battery.charging,
        'battery.charging_time': finite(battery.chargingTime),
        'battery.discharging_time': finite(battery.dischargingTime),
        'battery.level': battery.level,
        'storage.temporary.usage': estimate.usage,
        'storage.temporary.quota': estimate.quota,
        'client_hints.brands': brands(hints.brands),
        'client_hints.full_version_list': brands(high.fullVersionList),
        'client_hints.mobile': hints.mobile,
        'client_hints.platform': hints.platform,
        'client_hints.platform_version': high.platformVersion,
        'client_hints.architecture': high.architecture,
        'client_hints.bitness': high.bitness,
        'client_hints.wow64': high.wow64,
        'client_hints.model': high.model,
        'client_hints.form_factors': Array.from(high.formFactors),
        'webgl.vendor': rendererInfo ? gl.getParameter(rendererInfo.UNMASKED_VENDOR_WEBGL) : '',
        'webgl.renderer': rendererInfo ? gl.getParameter(rendererInfo.UNMASKED_RENDERER_WEBGL) : '',
        'keyboard.layout': layoutEntries,
        'media.containers': mediaContainers,
        'media.self_contained': mediaSelfContained,
        'media.hardware_decode': mediaHardware,
        'media.webrtc': mediaWebrtc,
        'media.hardware_encode': mediaEncodeHardware,
        'media.devices': mediaDevices,
        'media.constraints': supportedConstraints,
        'speech.voices': speechVoices,
        'permissions.names': permissionNames,
        'permissions.granted': permissionGranted,
        'permissions.spec_names': permissionSpecNames,
        'gpu.wgsl_features': wgslFeatures,
        'plugins': Array.from(navigator.plugins).map(plugin => ({
            'name': plugin.name,
            'filename': plugin.filename,
            'description': plugin.description,
            'mime_types': Array.from(plugin).map(mime => ({
                'type': mime.type,
                'suffixes': mime.suffixes,
                'description': mime.description,
            })),
        })),
    });
})()
"""


#: The script the browser runs: the media probes are the parser's own vocabulary.
COLLECT_SCRIPT = (_COLLECT_TEMPLATE
                  .replace("__MEDIA_CANDIDATES__", json.dumps(probe_candidates()))
                  .replace("__MEDIA_WEBRTC_TYPES__", json.dumps(list(WEBRTC_CONTENT_TYPES)))
                  .replace("__MEDIA_WEBRTC_FAMILIES__", json.dumps(WEBRTC_VIDEO_FAMILIES))
                  .replace("__PERMISSION_NAMES__", json.dumps(sorted(PERMISSION_NAMES))))


#: What a page enumerates: the window's names, each prototype's members, each interface object's
#: own members (where ``CSS.animationWorklet`` lives), and the iterator machinery per prototype.
SURFACE_SCRIPT = """
(() => {
    const prototypes = {};
    const objects = {};
    const iterators = {};
    const read = (name, prototype) => {
        if (!name || !prototype || typeof prototype !== 'object' || name in prototypes) return;
        try { prototypes[name] = Object.getOwnPropertyNames(prototype).sort(); } catch (error) {}
    };
    const readObject = (name, value) => {
        if (!name || !value || name in objects) return;
        try { objects[name] = Object.getOwnPropertyNames(value).sort(); } catch (error) {}
    };
    // The iterator machinery (``template.array`` states which).  Descriptors up the chain only:
    // a plain read would run ``CSSMathMax.prototype.values``, a getter that is not an iterator.
    const find = (start, key) => {
        let walk = start;
        while (walk) {
            let descriptor = null;
            try { descriptor = Object.getOwnPropertyDescriptor(walk, key); } catch (error) {}
            if (descriptor) return descriptor;
            try { walk = Object.getPrototypeOf(walk); } catch (error) { walk = null; }
        }
        return null;
    };
    const isFunction = (descriptor) => !!descriptor && typeof descriptor.value === 'function';
    const readIterator = (name, prototype) => {
        if (!name || !prototype || name in iterators) return;
        try {
            const four = ['entries', 'keys', 'values', 'forEach'].filter(
                (member) => isFunction(find(prototype, member)));
            iterators[name] = {iterator: isFunction(find(prototype, Symbol.iterator)) ? 1 : 0,
                               four: four.length};
        } catch (error) {}
    };
    for (const name of Object.getOwnPropertyNames(window)) {
        let value = null;
        try { value = window[name]; } catch (error) { value = null; }
        if (typeof value === 'function') {
            try { read(name, value.prototype); } catch (error) {}
            try { readIterator(name, value.prototype); } catch (error) {}
            readObject(name, value);
        } else if (value && typeof value === 'object') {
            // A namespace is an object, not a function, and has no ``.prototype`` to read.
            readObject(name, value);
        }
    }
    // Interfaces without a global constructor are read off the objects that carry one.
    const makers = [() => document, () => document.body, () => document.createElement('div'),
                    () => document.createElement('canvas'), () => document.createElement('iframe'),
                    () => document.createElement('video'), () => document.createTextNode(''),
                    () => document.createComment(''), () => new Image(),
                    () => new CSSStyleSheet()];
    for (const make of makers) {
        try {
            const prototype = Object.getPrototypeOf(make());
            read(prototype.constructor.name, prototype);
            readIterator(prototype.constructor.name, prototype);
        } catch (error) {}
    }
    return JSON.stringify({
        keys: Object.keys(window).sort(),
        own: Object.getOwnPropertyNames(window).sort(),
        prototypes,
        objects,
        iterators});
})()"""


class CollectionError(RuntimeError):
    """The browser could not be read, with the reason."""


def find_chrome(explicit=None):
    """The browser to read, or an error saying how to point at one."""
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise CollectionError(f"--chrome {explicit} does not exist")
        return path
    override = os.environ.get(CHROME_ENV)
    if override:
        path = Path(override)
        if not path.exists():
            raise CollectionError(f"{CHROME_ENV}={override} does not exist")
        return path
    for candidate in CHROME_PATHS:
        path = Path(os.path.expandvars(candidate))
        if path.exists():
            return path
    raise CollectionError(
        f"no Chrome found in {', '.join(CHROME_PATHS)};"
        f" set {CHROME_ENV} to the browser to read"
    )


def read_devtools_port(profile_dir, *, timeout, poll=0.05):
    """``(port, browser_path)`` from the file Chrome writes once it is listening."""
    path = Path(profile_dir) / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while True:
        # Chrome writes the file as this reads it: half a file or a share violation means "not yet".
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        if len(lines) >= 2 and lines[0].strip().isdigit():
            return int(lines[0]), lines[1].strip()
        if time.monotonic() >= deadline:
            raise CollectionError(f"Chrome wrote no usable DevToolsActivePort within {timeout}s")
        time.sleep(poll)


def _http_json(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class CdpSession:
    """A blocking DevTools connection: one request at a time, answers matched by id."""

    def __init__(self, url, *, timeout=30.0):
        from websockets.sync.client import connect

        self._timeout = timeout
        self._next_id = 0
        self._socket = connect(
            url, max_size=None, open_timeout=timeout, close_timeout=timeout, proxy=None
        )

    def call(self, method, **params):
        self._next_id += 1
        request_id = self._next_id
        self._socket.send(json.dumps({"id": request_id, "method": method, "params": params}))
        while True:
            message = json.loads(self._socket.recv(timeout=self._timeout))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise CollectionError(f"{method} failed: {message['error'].get('message')}")
            return message.get("result", {})

    def evaluate(self, expression):
        result = self.call(
            "Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True
        )
        details = result.get("exceptionDetails")
        if details:
            raised = details.get("exception", {}).get("description") or details.get("text")
            raise CollectionError(f"the page cannot answer: {raised}")
        return result["result"].get("value")

    def close(self):
        self._socket.close()


class _LocalPage(BaseHTTPRequestHandler):
    """One minimal document: localhost counts as trustworthy, so hints exist."""

    def do_GET(self):
        body = b"<!doctype html><html><head><title>environment</title></head><body></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments):
        pass


class _QuietServer(ThreadingHTTPServer):
    """A browser dropping its keep-alive connection is not an error to report."""

    def handle_error(self, request, client_address):
        pass


def _local_origin():
    """``(server, url)`` for a document on localhost."""
    server = _QuietServer(("127.0.0.1", 0), _LocalPage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://localhost:{server.server_address[1]}/"


def _page_target(port, timeout):
    targets = _http_json(f"http://127.0.0.1:{port}/json/list", timeout)
    for target in targets:
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
            return target["webSocketDebuggerUrl"]
    return None


def _open_page(port, browser_url, timeout):
    browser = CdpSession(browser_url, timeout=timeout)
    try:
        browser.call("Target.createTarget", url="about:blank")
    finally:
        browser.close()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = _page_target(port, timeout)
        if url is not None:
            return url
        time.sleep(0.05)
    raise CollectionError("Chrome opened no page to read from")


def _session(port, timeout):
    """``(session, browser)`` for a page target, opening one when there is none."""
    version = _http_json(f"http://127.0.0.1:{port}/json/version", timeout)
    url = _page_target(port, timeout) or _open_page(port, version["webSocketDebuggerUrl"], timeout)
    return CdpSession(url, timeout=timeout), str(version.get("Browser", ""))


def _navigate(session, url, timeout):
    session.call("Page.navigate", url=url)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if session.evaluate("document.readyState") == "complete":
                return
        except CollectionError:
            pass
        time.sleep(0.05)
    raise CollectionError(f"the local page {url} never finished loading")


def read_pages(chrome, scripts, *, headed=False, accept_lang=None, window_size=None,
               extra_args=(), timeout=30.0):
    """``(values, source)`` for several expressions read from one browser session.

    Every answer comes from the same page, so facts read together describe one browser.
    """
    #: Chrome can still hold a file in its profile directory when it exits: a cache the operating
    #: system has not released yet must not turn a whole reading into a failed run.
    with tempfile.TemporaryDirectory(prefix="pyv8c-chrome-",
                                     ignore_cleanup_errors=True) as profile:
        server, origin = _local_origin()
        arguments = [
            str(chrome),
            f"--user-data-dir={profile}",
            "--remote-debugging-port=0",
            # A non-browser client has to be allowed to attach to the socket.
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            # Instrumentation, not environment: without this the browser reports ``navigator.webdriver`` as true.
            "--disable-blink-features=AutomationControlled",
        ]
        if not headed:
            arguments.append("--headless=new")
        if accept_lang:
            arguments.append(f"--accept-lang={accept_lang}")
        if window_size:
            arguments.append(f"--window-size={window_size}")
        arguments.extend(str(argument) for argument in extra_args)

        process = subprocess.Popen(
            arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            port, _path = read_devtools_port(profile, timeout=min(timeout, 15.0))
            session, browser = _session(port, timeout)
            try:
                _navigate(session, origin, timeout)
                values = [session.evaluate(script) for script in scripts]
            finally:
                session.close()
            source = {
                "browser": browser,
                "executable": str(chrome),
                "platform": sys.platform,
                "collected": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "arguments": [argument for argument in arguments[1:] if not argument.startswith("--user-data-dir")],
            }
            return values, source
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            server.shutdown()
            server.server_close()


#: The locations a browser can be asked about: a prototype object, and an interface object.
#: ``instance`` members live on instances the page does not hand out per interface.
MEASURED_LOCATIONS = ("prototype", "interface")


def idl_view(idl=None):
    """``(declared members by location, digest)`` of the document a run measures against.

    ``declared`` is ``{interface: {location: {names}}}``: what the document says the interface
    should carry at each location a browser can answer for.  No profile narrows it, because a
    run is measuring what this browser installs against the whole document.

    Reading it needs no native extension, so a collection runs without pyv8.
    """
    idl = idl if idl is not None else Idl.load(profile=None)
    declared = {
        name: {location: {member.name for member in idl.interface(name).own_members(location)}
               for location in MEASURED_LOCATIONS}
        for name in idl.names()
    }
    return declared, idl.digest


#: Where each platform keeps the profile a normal visit uses.
USER_DATA_DIRS = (
    r"%LOCALAPPDATA%\Google\Chrome\User Data",
    "~/Library/Application Support/Google/Chrome",
    "~/.config/google-chrome",
)


def default_user_data():
    """The browser's own profile directory on this machine, or ``None``."""
    for candidate in USER_DATA_DIRS:
        path = Path(os.path.expandvars(candidate)).expanduser()
        if path.is_dir():
            return path
    return None


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}


def _profile_settings(directory):
    """A profile's extension table, which the browser keeps in its own preferences."""
    for name in ("Secure Preferences", "Preferences"):
        settings = ((_read_json(Path(directory) / name).get("extensions") or {})
                    .get("settings") or {})
        if settings:
            return settings
    return {}


def extension_entries(declared):
    """Web-accessible resources in Chrome's own ``{resources, matches}`` shape."""
    entries = []
    for item in declared or []:
        if isinstance(item, str):
            entries.append({"resources": [item], "matches": ["<all_urls>"]})
        elif isinstance(item, dict) and item.get("resources"):
            entries.append({
                "resources": [str(resource) for resource in item["resources"]],
                "matches": [str(match) for match in (item.get("matches") or ["<all_urls>"])],
            })
    return entries


def profile_extensions(user_data_dir, profile="Default"):
    """``id -> declared resources`` for the extensions a profile has installed and enabled.

    Read from the profile itself: an extension that is suspended or blocked answers a
    page with nothing, and one that exposes no resource to pages cannot answer a probe.
    """
    root = Path(user_data_dir).expanduser()
    directory = root / profile if (root / profile / "Extensions").is_dir() else root
    settings = _profile_settings(directory)
    declared = {}
    for extension in sorted((directory / "Extensions").iterdir()):
        if not extension.is_dir():
            continue
        if (settings.get(extension.name) or {}).get("disable_reasons"):
            continue
        versions = sorted(item for item in extension.iterdir() if (item / "manifest.json").exists())
        if not versions:
            continue
        entries = extension_entries(_read_json(versions[-1] / "manifest.json")
                                    .get("web_accessible_resources"))
        if entries:
            declared[extension.name] = entries
    return declared


def read_profile(chrome, view=None, extensions=None, **options):
    """``(payload, surface, source)``: both readings a run makes, from one session.

    *extensions* is the profile's extension table, read from the browser's own profile:
    a page cannot enumerate extensions, and the session read here runs with none.
    """
    declared, digest = view if view is not None else idl_view()
    values, source = read_pages(chrome, (COLLECT_SCRIPT, SURFACE_SCRIPT), **options)
    payload = json.loads(values[0])
    payload["extensions"] = dict(extensions or {})
    return payload, build_surface(json.loads(values[1]), declared, digest), source


def build_environment(payload):
    """The payload as an environment, refusing anything but a whole profile."""
    if set(payload) != set(COLLECTED_KEYS):
        raise CollectionError(
            f"profile keys are not the collected ones:"
            f" missing {sorted(set(COLLECTED_KEYS) - set(payload))},"
            f" unknown {sorted(set(payload) - set(COLLECTED_KEYS))}"
        )
    return BrowserEnvironment(payload)


def _gaps_for(readings, declared, location):
    """``{interface: names the browser does not have}`` at one location.

    Only names the baseline declares can be a gap: what it never declared is not a difference.
    """
    gaps = {}
    for name, names in (readings or {}).items():
        known = (declared.get(str(name)) or {}).get(location)
        if not known or not isinstance(names, list):
            continue
        missing = sorted(known - {str(entry) for entry in names})
        if missing:
            gaps[str(name)] = missing
    return gaps


#: The four named methods ``ArrayProto.iterable`` borrows on top of ``@@iterator``.
ITERATOR_MEMBERS = ("entries", "keys", "values", "forEach")


def _array_protos(readings, known):
    """``{interface: bits}`` for the interfaces the baseline declares.

    ``@@iterator`` alone is ``ArrayProto.iterator``; with all four named methods it is
    ``ArrayProto.iterable``; an interface answering no ``@@iterator`` records nothing, the field's
    third value (as ``Cache``, whose ``keys`` is an async method, and ``CSSMathMax``, whose
    ``values`` is a length, measure).

    The iterator with only *some* of the four is a shape the field cannot state, so the run stops
    rather than pick one: measured against Chrome 154, no declared interface is in that position.
    """
    protos = {}
    for name, reading in (readings or {}).items():
        name = str(name)
        if name not in known or not isinstance(reading, dict) or not reading.get("iterator"):
            continue
        count = reading.get("four")
        if count == 4:
            protos[name] = ArrayProto.iterable
        elif count == 0:
            protos[name] = ArrayProto.iterator
        else:
            raise CollectionError(
                f"{name} answers @@iterator together with {count} of"
                f" {', '.join(ITERATOR_MEMBERS)}: array states either the iterator alone or that"
                " iterator with all four, and no value carries this")
    return protos


def build_surface(payload, declared=None, against=""):
    """What the browser does not install, as the profile's ``bindings`` section.

    ``exposed`` covers the globals; ``omits`` is measured against ``declared``
    (``{interface: {location: {names}}}``), and ``against`` records which IDL document those names
    came from.  ``prototype`` members are read from each prototype object and ``interface`` members
    from each interface object, which is where a namespace keeps its own.

    ``array_protos`` states, in the vocabulary ``template.array`` and ``IdlSpec.array`` share,
    what each interface borrows from ``Array.prototype`` in this release -- measured rather than
    assumed, so a document that is wider than the release stays narrow here.
    """
    declared = declared or {}
    return {
        "keys": list(payload["keys"]),
        "exposed": list(payload["own"]),
        "omits": {
            "prototype": _gaps_for(payload.get("prototypes"), declared, "prototype"),
            "interface": _gaps_for(payload.get("objects"), declared, "interface"),
        },
        "array_protos": _array_protos(payload.get("iterators"), set(declared)),
        "against": str(against or ""),
    }


def build_profile(payload, surface, source):
    """The document a run writes: the environment keys, and what the browser installs of the IDL.

    A non-finite number travels as its name, so a strict JSON parser accepts the file.
    """
    document = {"schema": SCHEMA_VERSION, "source": dict(source)}
    document.update(build_environment(payload).to_dict())
    document["bindings"] = surface
    return json_value(document)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chrome", help=f"browser executable; {CHROME_ENV} overrides the search")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT,
                        help="where to write the profile, or - for standard output (default: %(default)s)")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--accept-lang", help="pin the languages the browser reports")
    parser.add_argument("--window-size", help="pin the window size as WIDTH,HEIGHT")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for the browser")
    parser.add_argument("--chrome-arg", action="append", default=[], help="extra browser argument")
    parser.add_argument("--profile", type=Path,
                        help="Chrome user-data-dir (or a profile inside it) whose installed "
                             "extensions the profile records; default: this machine's own")
    arguments = parser.parse_args(argv)

    try:
        chrome = find_chrome(arguments.chrome)
        user_data = arguments.profile or default_user_data()
        extensions = profile_extensions(user_data) if user_data is not None else {}
        payload, surface, source = read_profile(
            chrome,
            extensions=extensions,
            headed=arguments.headed,
            accept_lang=arguments.accept_lang,
            window_size=arguments.window_size,
            extra_args=arguments.chrome_arg,
            timeout=arguments.timeout,
        )
        source["extensions"] = str(user_data) if user_data is not None else ""
        # Only the keys the browser answered; the rest stay defaults, unclaimed.
        document = build_profile(payload, surface, source)
    except (CollectionError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    text = json.dumps(document, indent=2) + "\n"
    if str(arguments.out) == "-":
        sys.stdout.write(text)
        return 0
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(text, encoding="utf-8")
    print(
        f"wrote {arguments.out}: {len(surface['exposed'])} exposed names,"
        f" {len(surface['omits']['prototype'])} interfaces with prototype gaps,"
        f" {len(surface['omits']['interface'])} with interface-object gaps,"
        f" {len(surface['array_protos'])} with an array value,"
        f" {len(surface['keys'])} keys, {len(payload['extensions'])} extensions,"
        f" from {source.get('browser', 'unknown')}",
        file=sys.stderr,
    )
    return 0

__all__ = [
    "CHROME_ENV", "CHROME_PATHS", "COLLECT_SCRIPT", "DEFAULT_OUTPUT", "ITERATOR_MEMBERS",
    "SURFACE_SCRIPT", "CdpSession", "CollectionError", "idl_view", "build_environment",
    "build_profile", "build_surface", "default_user_data", "extension_entries", "find_chrome",
    "main", "profile_extensions", "read_devtools_port", "read_pages", "read_profile",
]


if __name__ == "__main__":
    sys.exit(main())
