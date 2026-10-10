"""Run one case here, and in the Chrome already installed on this machine.

The reference is named by path, not fetched: nothing is downloaded, and which browser
answered is part of every divergence report.  Point ``PYV8C_CHROME`` at another
``chrome.exe`` to compare against a different one.

Both sides run the same page and evaluate the same script once.  The reference
fulfils the document's URL itself, so the page has the origin ``corpus.URL`` says it
has -- cookie and URL behaviour need a real origin, not ``about:blank``.  What comes
back is a value or the name of a thrown error: an error *message* is written by the
engine for a human, so only the class is compared.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from corpus import Repro

REFERENCE_ENV = "PYV8C_CHROME"

CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
)

#: pyv8 spells an error ``PageTypeError``, Chrome writes ``TypeError: boom``; the class
#: is the part the two can be held to.
ERROR_NAME = re.compile(r"(?:V8)?([A-Z][A-Za-z]*Error)")


class ReferenceUnavailable(RuntimeError):
    """The reference browser cannot be run, with the reason to report."""


@dataclasses.dataclass(frozen=True)
class Observation:
    """What a browser did with a case: a value, or the error it raised.

    ``detail`` carries the engine's own wording of an error for the report only -- it
    is written for a human, so it is never compared.
    """

    value: Any = None
    error: str = ""
    detail: str = dataclasses.field(default="", compare=False)

    def __str__(self):
        if self.error:
            first_line = self.detail.splitlines()[0] if self.detail else ""
            return f"{self.error}: {first_line}" if first_line else self.error
        return json.dumps(self.value, sort_keys=True, default=repr)


def error_name(text, error=None):
    """The class of a reported error: named in the prose, or carried by the error.

    Playwright names the class in the message, pyv8's DOMException keeps it in
    ``name`` only, so the prose is read first and the error itself after that.
    """
    names = ERROR_NAME.findall(text or "")
    if names:
        return names[-1]
    for name in (getattr(error, "name", None), getattr(error, "_name", None)):
        if isinstance(name, str) and name:
            return name
    return ""


def normalize(value):
    """A value the two engines can be held to the same record by.

    The reference side hands back what it can serialize, and there a JavaScript
    ``null`` and an ``undefined`` are both simply absent.  pyv8's two singletons mean
    the same thing at that boundary, so they read as ``None`` here as well: a case
    that needs to tell them apart asks for ``typeof``, which is a string.
    """
    import pyv8

    if value is pyv8.Null or value is pyv8.Undefined:
        return None
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    return value


def chrome_executable():
    """``(path, "")`` for the Chrome to compare against, or ``(None, reason)``."""
    override = os.environ.get(REFERENCE_ENV)
    if override:
        path = Path(override)
        if path.exists():
            return path, ""
        return None, f"{REFERENCE_ENV}={override} does not exist"

    for candidate in CHROME_PATHS:
        path = Path(os.path.expandvars(candidate))
        if path.exists():
            return path, ""
    return None, (
        f"no Chrome found in {', '.join(CHROME_PATHS)}; set {REFERENCE_ENV} to the"
        f" chrome.exe to compare against"
    )


#: One engine for the whole run: a context per case isolates it just as well, and building the
#: engine per case costs more than the cases themselves.
_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from pyv8c import Browser

        _ENGINE = Browser(stub_logging="off")
    return _ENGINE


def observe_here(case: Repro) -> Observation:
    """What this environment observes for a case."""
    context = _engine().new_context()
    try:
        page = context.new_page()
        page.set_content(case.html, url=case.url)
        page.run_until_idle(timeout=1)
        try:
            return Observation(value=normalize(page.evaluate(case.script)))
        except BaseException as error:  # a script that throws is an observation too
            return Observation(
                error=error_name(str(error), error) or type(error).__name__, detail=str(error)
            )
    finally:
        context.close()


class ChromeReference:
    """A running local Chrome, reused for every case in a run."""

    def __init__(self, browser, executable, version):
        self._browser = browser
        self.executable = executable
        self.version = version

    @property
    def browser(self):
        """The live Playwright browser, for a test that drives it itself."""
        return self._browser

    def observe(self, case: Repro) -> Observation:
        page = self._browser.new_page()
        try:
            page.route(
                "**/*",
                lambda route: route.fulfill(
                    status=200, content_type="text/html", body=case.html
                ),
            )
            page.goto(case.url)
            if case.settle:
                # Chrome may deliver what the page waits for on a task after load;
                # this side is synchronous and has already run to idle by now.
                page.wait_for_timeout(int(case.settle * 1000))
            try:
                return Observation(value=normalize(page.evaluate(case.script)))
            except BaseException as error:
                return Observation(
                    error=error_name(str(error), error) or type(error).__name__, detail=str(error)
                )
        finally:
            page.close()


def _headless_chrome_pids():
    """The headless Chrome processes running now.

    Windows keeps them when a driver dies without saying goodbye, and a machine that keeps
    collecting them gets slower every run, so a run takes down the ones it started.
    """
    if os.name != "nt":
        return frozenset()
    script = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -match '--headless' } | "
              "Select-Object -ExpandProperty ProcessId")
    try:
        found = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                               capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    return frozenset(int(line) for line in found.stdout.split() if line.isdigit())


def _take_down(pids):
    """End the browsers *pids* names, with the children each one started."""
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue


@contextlib.contextmanager
def chrome_reference():
    """The locally installed Chrome, or :class:`ReferenceUnavailable` saying why not."""
    executable, reason = chrome_executable()
    if executable is None:
        raise ReferenceUnavailable(reason)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise ReferenceUnavailable(
            f"playwright is not installed ({error}); pip install playwright"
        ) from error

    playwright = sync_playwright().start()
    running = _headless_chrome_pids()
    try:
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
    except Exception as error:
        playwright.stop()
        raise ReferenceUnavailable(f"cannot start {executable}: {error}") from error
    try:
        yield ChromeReference(browser, executable, browser.version)
    finally:
        # ``browser.close()`` waits on Chrome's goodbye handshake -- up to forever on Windows --
        # so the run stops on a background thread and takes its own browsers down directly.
        threading.Thread(target=playwright.stop, daemon=True).start()
        _take_down(_headless_chrome_pids() - running)


def divergence(here: Observation, there: Observation, browser: str = "") -> str:
    """How two observations differ, or ``""`` when they agree."""
    if here == there:
        return ""
    where = f"Chrome {browser}" if browser else "the reference browser"
    return f"pyv8c observed {here}; {where} observed {there}"
