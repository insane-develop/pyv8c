"""The lazy value store, and the tables the owners declare."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pyv8")
pytestmark = pytest.mark.native

from pyv8c.engine.features import LazyValueStore  # noqa: E402
from pyv8c.web.bom import navigation, navigator, screen, url, window  # noqa: E402
from pyv8c.web.workers import api as workers  # noqa: E402

#: ``(module, factory, owner class, how the module reads a value)``
TABLES = (
    (navigator, navigator._navigator_values, navigator.Navigator, "value"),
    (screen, screen._screen_values, screen.Screen, "value"),
    (navigation, navigation._navigation_values, navigation.Navigation, "value"),
    (url, url._url_values, url.URL, "value"),
    (workers, workers._worker_values, workers.WorkerGlobalScope, "value"),
    (window, window._singleton_values, window.Window, "window_value"),
)

#: The owners a table closes over; nothing here is read until a factory runs.
STUBS = {
    "_navigator_values": lambda: SimpleNamespace(_environment=None, _services=None),
    "_screen_values": SimpleNamespace,
    "_navigation_values": SimpleNamespace,
    "_url_values": SimpleNamespace,
    "_worker_values": SimpleNamespace,
    "_singleton_values": SimpleNamespace,
}

READ_CALL = {
    "value": re.compile(r"\.value\(['\"]([A-Za-z]+)['\"]\)"),
    "window_value": re.compile(r"_window_value\(['\"]([A-Za-z]+)['\"]\)"),
}
CACHED_CALL = re.compile(r"_cache\(['\"]([A-Za-z]+)['\"]")


def table_of(factory):
    """The names a factory puts in its table, without building any owner."""
    return factory(STUBS[factory.__name__]())._factories


def read_names(module, read):
    """Every name a module asks a table for, by attribute or by method."""
    return set(READ_CALL[read].findall(Path(module.__file__).read_text(encoding="utf-8")))


def cached_names(module):
    """Names a module fills itself, such as a twin built beside its sibling."""
    return set(CACHED_CALL.findall(Path(module.__file__).read_text(encoding="utf-8")))


def test_an_unknown_name_names_itself():
    with pytest.raises(AttributeError, match="no value 'nope'"):
        LazyValueStore({}).value("nope")


def test_no_table_declares_a_name_nothing_reads():
    for module, factory, _owner, read in TABLES:
        dead = sorted(set(table_of(factory)) - read_names(module, read))
        assert not dead, f"{module.__name__} declares names nothing reads: {dead}"


def test_no_module_reads_a_name_no_table_holds():
    for module, factory, _owner, read in TABLES:
        served = set(table_of(factory)) | cached_names(module)
        unknown = sorted(read_names(module, read) - served)
        assert not unknown, f"{module.__name__} reads names no table holds: {unknown}"
