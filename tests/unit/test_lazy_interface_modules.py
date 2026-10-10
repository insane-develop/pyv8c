"""Optional interface modules load only at their first observable use."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_optional_interface_modules_stay_unimported_until_first_use():
    """Use a child process because module imports are process-global caches."""
    code = textwrap.dedent("""
        import json
        import sys
        from pyv8c import Browser

        modules = {
            "idb": "pyv8c.web.bom.indexeddb",
            "navigator": "pyv8c.web.bom.navigator",
            "websocket": "pyv8c.web.websocket",
            "worker": "pyv8c.web.workers.api",
            "filesystem": "pyv8c.web.bom.filesystem",
        }
        browser = Browser(stub_logging="off")
        try:
            page = browser.new_context().new_page()
            output = {"startup": {key: module in sys.modules for key, module in modules.items()}}
            output["indexeddb_identity"] = page.evaluate("indexedDB === indexedDB")
            output["after_idb"] = {key: module in sys.modules for key, module in modules.items()}
            output["idb_type"] = page.evaluate("typeof IDBKeyRange")
            output["navigator_identity"] = page.evaluate("navigator === navigator")
            output["after_navigator"] = {key: module in sys.modules for key, module in modules.items()}
            output["websocket_type"] = page.evaluate("typeof WebSocket")
            output["after_websocket"] = {key: module in sys.modules for key, module in modules.items()}
            output["worker_type"] = page.evaluate("typeof Worker")
            output["after_worker"] = {key: module in sys.modules for key, module in modules.items()}
            output["file_type"] = page.evaluate("typeof File")
            output["after_file"] = {key: module in sys.modules for key, module in modules.items()}
            output["idb_group"] = [
                name for name in page.binding_session.materialized_interfaces
                if name.startswith("IDB")
            ]
            print(json.dumps(output))
        finally:
            browser.close()
    """)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT.parent / "_pyv8"), str(ROOT)))
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=environment,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    def loaded(idb=False, navigator=False, websocket=False, worker=False, filesystem=False):
        return {"idb": idb, "navigator": navigator, "websocket": websocket,
                "worker": worker, "filesystem": filesystem}

    assert output["startup"] == loaded()
    assert output["indexeddb_identity"] is True
    assert output["after_idb"] == loaded(idb=True)
    assert output["idb_type"] == "function"
    assert output["navigator_identity"] is True
    assert output["after_navigator"] == loaded(idb=True, navigator=True)
    assert output["websocket_type"] == "function"
    assert output["after_websocket"] == loaded(idb=True, navigator=True, websocket=True)
    assert output["worker_type"] == "function"
    assert output["after_worker"] == loaded(idb=True, navigator=True, websocket=True, worker=True)
    assert output["file_type"] == "function"
    assert output["after_file"] == loaded(idb=True, navigator=True, websocket=True, worker=True, filesystem=True)
    assert {"IDBFactory", "IDBKeyRange", "IDBDatabase", "IDBTransaction"}.issubset(output["idb_group"])


def test_feature_runtime_loads_an_explicit_feature_once():
    pytest.importorskip("pyv8", reason="the interface decorator reaches the native extension")
    from types import SimpleNamespace

    from pyv8c.engine.features import FeatureCatalog, FeatureImplementation, FeatureRuntime, FeatureSpec
    from pyv8c.idl import interface

    @interface("LazyUnitInterface", None)
    class LazyUnitInterface:
        pass

    calls = []

    def loader(module_name):
        assert module_name == "tests.lazy_unit"
        calls.append(True)
        return SimpleNamespace(FEATURE=FeatureImplementation({"LazyUnitInterface": LazyUnitInterface}))

    catalog = FeatureCatalog((FeatureSpec("unit", "tests.lazy_unit", ("LazyUnitInterface",)),))
    runtime = FeatureRuntime(catalog, importer=loader)

    assert runtime.interface("LazyUnitInterface") is LazyUnitInterface
    assert runtime.interface("LazyUnitInterface") is LazyUnitInterface
    assert calls == [True]
    assert runtime.implementations["LazyUnitInterface"] is LazyUnitInterface


def test_feature_runtime_rejects_partial_exports_without_caching_them():
    from types import SimpleNamespace

    from pyv8c.engine.features import FeatureCatalog, FeatureImplementation, FeatureRuntime, FeatureSpec

    runtime = FeatureRuntime(
        FeatureCatalog((FeatureSpec("broken", "tests.broken_feature", ("Expected",)),)),
        importer=lambda _: SimpleNamespace(FEATURE=FeatureImplementation({})),
    )

    with pytest.raises(RuntimeError, match="interfaces differ"):
        runtime.load("broken")
    assert not runtime.loaded_features
    assert not runtime.implementations


def test_feature_runtime_reports_cycles_before_importing_a_module():
    from pyv8c.engine.features import FeatureCatalog, FeatureRuntime, FeatureSpec

    runtime = FeatureRuntime(
        FeatureCatalog((
            FeatureSpec("first", "tests.first_feature", ("First",), requires=("second",)),
            FeatureSpec("second", "tests.second_feature", ("Second",), requires=("first",)),
        )),
        importer=lambda _: pytest.fail("cycle must be detected before module import"),
    )

    with pytest.raises(RuntimeError, match="first -> second -> first"):
        runtime.load("first")


def test_lazy_value_store_caches_none_and_retries_failed_creation():
    from pyv8c.engine.features import LazyValueStore

    class Values(LazyValueStore):
        def __init__(self):
            super().__init__()
            self.calls = []

        def _create_value(self, name):
            self.calls.append(name)
            if name == "retry" and self.calls.count(name) == 1:
                raise RuntimeError("creation failed")
            return None if name == "empty" else object()

    values = Values()
    assert values.value("empty") is None
    assert values.value("empty") is None
    assert values.calls == ["empty"]
    assert values.cached_names == {"empty"}

    with pytest.raises(RuntimeError, match="creation failed"):
        values.value("retry")
    assert "retry" not in values.cached_names
    created = values.value("retry")
    assert values.value("retry") is created
    assert values.calls == ["empty", "retry", "retry"]
