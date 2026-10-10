"""``window.chrome``, the object Chromium installs through V8 extras.

The global is writable and enumerable but non-configurable, and its members are
native functions (``String(chrome.csi)`` reports native code).  It is built by
:data:`_BOOTSTRAP` in JavaScript rather than by a host class, so its prototype
stays ``Object.prototype``.
"""
import time

import pyv8

__all__ = ["ChromeBindings", "install_window_chrome"]


# Blink reports a normal navigation as 15 (WebNavigationType::kNavigationTypeOther).
_TRANSITION_TYPE = 15
# Milestones in ms after navigationStart; an unreached one reports 0, as Chromium
# does, and the values are the usual order of magnitude for a parsed document.
_MILESTONES = (
    ("requestTime", 0.4),
    ("startLoadTime", 0.6),
    ("commitLoadTime", 22.5),
    ("finishDocumentLoadTime", 45.2),
    ("finishLoadTime", 78.4),
    ("firstPaintTime", 62.1),
)
_LOAD_TIME_FIELDS = (
    "requestTime", "startLoadTime", "commitLoadTime", "finishDocumentLoadTime",
    "finishLoadTime", "firstPaintTime", "firstPaintAfterLoadTime",
    "navigationType", "wasFetchedViaSpdy", "wasNpnNegotiated",
    "npnNegotiatedProtocol", "wasAlternateProtocolAvailable", "connectionInfo",
)
_APP_FUNCTIONS = (
    "getDetails", "getIsInstalled", "installState", "runningState",
)
# Windows' time.time() only ticks every ~15ms, while Chromium reports
# sub-millisecond timings: anchor the high-resolution clock to the epoch once.
_EPOCH_OFFSET_MS = time.time() * 1000.0 - time.perf_counter() * 1000.0


def _now_ms():
    """Current time in epoch milliseconds with the resolution Chrome reports."""
    return time.perf_counter() * 1000.0 + _EPOCH_OFFSET_MS
# Temporary global handing the native functions to the bootstrap; the bootstrap
# deletes it in the same synchronous step.
_BRIDGE_GLOBAL = "__pyv8cChromeHost__"

_BOOTSTRAP = """
(() => {
    const host = globalThis.__BRIDGE__;
    const app = {
        isInstalled: false,
        getDetails: host.getDetails,
        getIsInstalled: host.getIsInstalled,
        installState: host.installState,
        runningState: host.runningState,
        InstallState: {DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'},
        RunningState: {CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'},
    };
    const chrome = {loadTimes: host.loadTimes, csi: host.csi, app: app};
    try {
        Object.defineProperty(globalThis, 'chrome', {
            value: chrome, writable: true, enumerable: true, configurable: false,
        });
    } finally {
        delete globalThis.__BRIDGE__;
    }
})();
""".replace("__BRIDGE__", _BRIDGE_GLOBAL)


class ChromeBindings:
    """Python side of the ``chrome`` object; its methods back the native functions."""

    def __init__(self, services=None):
        self._services = services
        self._document = None
        self._navigation_start = 0.0

    def _navigation_started_at(self):
        """Epoch milliseconds of the current document's navigationStart; a new
        document restarts the clock.
        """
        services = self._services
        document = getattr(getattr(services, "document_services", None), "document", None)
        if document is not self._document:
            self._document = document
            self._navigation_start = _now_ms()
        return self._navigation_start

    def loadTimes(self):
        start = self._navigation_started_at()
        now = _now_ms()
        values = {name: start + offset for name, offset in _MILESTONES}
        values = {name: value if value <= now else 0.0 for name, value in values.items()}
        values.update(
            firstPaintAfterLoadTime=0.0,
            navigationType="Other",
            wasFetchedViaSpdy=True,
            wasNpnNegotiated=True,
            npnNegotiatedProtocol="h2",
            wasAlternateProtocolAvailable=False,
            connectionInfo="h2",
        )
        return {field: values[field] for field in _LOAD_TIME_FIELDS}

    def csi(self):
        start = self._navigation_started_at()
        now = _now_ms()
        loaded = start + dict(_MILESTONES)["finishLoadTime"]
        return {
            "startE": start,
            "onloadT": loaded if loaded <= now else 0.0,
            "pageT": now,
            "tran": _TRANSITION_TYPE,
        }

    def getDetails(self):
        return pyv8.Null

    def getIsInstalled(self):
        return False

    def installState(self):
        return "not_installed"

    def runningState(self):
        return "cannot_run"


def install_window_chrome(context, services=None):
    """Install ``window.chrome`` for one Window realm, before any page script as
    Chromium does; worker globals do not get one.
    """
    bindings = ChromeBindings(services)
    bridge = {name: getattr(bindings, name) for name in ("loadTimes", "csi", *_APP_FUNCTIONS)}
    # Installing the object is host work: no trace event may be reported for the
    # bridge global or the descriptor, matching Chromium's own V8 extras.
    tracing = bool(getattr(context, "trace", False))
    if tracing:
        context.trace = False
    try:
        context.glob[_BRIDGE_GLOBAL] = bridge
        context.exec_js(_BOOTSTRAP, filename="chrome.js")
    finally:
        if tracing:
            context.trace = True
