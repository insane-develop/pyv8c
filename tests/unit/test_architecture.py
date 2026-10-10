"""Guard the package layering introduced by the domain-package refactor.

One rule about errors lives with these: an error a realm can be shown is built where a
page could be the cause of one, which is ``web/``.  ``tests/unit/test_error_provenance.py``
holds it, with the registered exceptions and the reason each cannot reach a realm.
"""

import ast
import importlib
from pathlib import Path

import pytest

import pyv8c

ROOT = Path(__file__).resolve().parents[2] / "pyv8c"
DOMAIN_PACKAGES = {"web", "web/dom", "web/bom", "web/fetch", "web/workers"}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            yield node.level, node.module or ""


#: What the root package promises: the object model, the errors an application
#: catches, and the network extension points.  Everything else -- bundled
#: engines, web interface implementations, internal helpers -- belongs to the
#: module that owns it.
PUBLIC_SURFACE = {
    "Browser", "BrowserContext", "Page", "DevToolsHandle", "DevToolsOptions",
    "BrowserEnvironment",
    "HttpTransport", "WebSocketTransport", "NetworkBackend",
    "register_transport", "register_ws_transport", "transport_names",
    "NetworkRequest", "NetworkResponse", "NetworkUnavailableError",
    "PageError", "PageTypeError", "PageRangeError", "PageReferenceError", "PageSyntaxError",
    "PageURIError", "PageEvalError", "DOMException",
    "HostFailure",
}


def test_the_root_exports_only_the_public_surface():
    assert set(pyv8c.__all__) == PUBLIC_SURFACE
    # The root must remain importable for the standalone Chromium extractor;
    # resolving browser classes themselves requires the optional native pyv8.
    assert callable(pyv8c.__getattr__)
    for internal in ("CronetTransport", "CurlCffiTransport", "WebSocketsTransport"):
        with pytest.raises(AttributeError):
            getattr(pyv8c, internal)


def test_core_has_no_relative_dependency_on_higher_layers():
    for path in (ROOT / "foundation").glob("*.py"):
        for level, module in _imports(path):
            assert not (level > 1 or module.startswith(("engine", "web", "platform"))), path


def test_domain_packages_do_not_depend_on_runtime_or_offline_tools():
    for package in DOMAIN_PACKAGES:
        for path in (ROOT / package).glob("*.py"):
            for _level, module in _imports(path):
                assert not module.startswith("platform"), path
                assert not module.startswith("tools"), path


#: Implementation modules the Feature runtime loads on first use, mapped to the
#: package they belong to and the relative level sibling features sit at.
OPTIONAL_FEATURES = {
    "web/bom/navigator.py": ("bom", 2),
    "web/bom/indexeddb.py": ("bom", 2),
    "web/bom/filesystem.py": ("bom", 2),
    "web/websocket.py": ("web", 1),
    "web/workers/api.py": ("workers", 2),
    "web/workers/runtime.py": ("workers", 2),
}

#: Web packages every feature may build on at import time.  ``page_errors`` is the layer
#: that owns the error vocabulary, so every feature may name it.
SHARED_WEB_PACKAGES = ("dom", "fetch", "page_errors")

#: ``bom`` helper modules that hold no interface behind them.
SHARED_BOM_MODULES = ("bom.scheduling", "bom.utils")


def test_optional_features_build_only_on_shared_layers():
    """Loading one optional feature must not drag in another one.

    A module-body import of a sibling feature would defeat the on-demand
    contract in docs/ARCHITECTURE.md; its runtime half is the ``sys.modules``
    probe in tests/unit/test_lazy_interface_modules.py.  Only import-time
    imports are inspected: a call body may reach a sibling feature lazily.

    ``dom`` and ``fetch`` are shared by every feature, but ``bom`` holds
    sibling features, so only the helper modules that carry no interface behind
    them may be named from it.
    """
    for relative, (own_package, sibling_level) in OPTIONAL_FEATURES.items():
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.level != sibling_level:
                continue
            module = node.module or ""
            shared = module in SHARED_BOM_MODULES or module.split(".")[0] in SHARED_WEB_PACKAGES
            assert shared or module.split(".")[0] == own_package, (relative, module)


def test_bindings_do_not_depend_on_runtime():
    """bindings must reach realm services through core, never through Page."""
    for path in (ROOT / "engine").glob("*.py"):
        for _level, module in _imports(path):
            assert not module.startswith("platform"), path
        source = path.read_text(encoding="utf-8")
        assert "_make_promise" not in source, path
        assert "_js_error_value" not in source, path


def test_no_implicit_current_page_state_remains():
    forbidden = ("current_page", "set_current_page", "_current_page")
    for path in ROOT.rglob("*.py"):
        assert not any(token in path.read_text(encoding="utf-8") for token in forbidden), path


def test_offline_scripts_do_not_import_browser_runtime():
    for path in (ROOT / "tools").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "platform.browser" not in source
        assert "from pyv8c import Browser" not in source


def test_every_offline_script_has_only_a_wrapper_in_the_repository_tools():
    """``tools/X.py`` hands over to ``pyv8c/tools/X.py`` and holds no logic."""
    pytest.importorskip("pyv8", reason="imports every tool, and two of them reach the extension")
    for path in (ROOT / "tools").glob("*.py"):
        if path.name.startswith("__"):
            continue
        implementation = importlib.import_module(f"pyv8c.tools.{path.stem}")
        shell = importlib.import_module(f"tools.{path.stem}")
        assert shell.main is implementation.main, path


def test_the_idl_and_the_binder_each_have_one_home():
    bindings_source = (ROOT / "engine" / "bindings.py").read_text(encoding="utf-8")
    extractor_source = (ROOT / "tools" / "extract_idl.py").read_text(encoding="utf-8")
    idl_source = (ROOT / "engine" / "idl.py").read_text(encoding="utf-8")
    binder_source = (ROOT / "engine" / "binder.py").read_text(encoding="utf-8")
    # One collection: the document, the profile that narrows it, and the lookups over both.
    for declaration in ("class Idl", "class IdlInterface", "class IdlMember", "class Profile",
                        "class RealmPlan", "def write_idl"):
        assert declaration in idl_source, declaration
    # Turning it into native specs, and the shells and realm callbacks that go with it, are one
    # module, and ``Binder`` is the only concept in it.
    for declaration in ("class Binder", "def _parameters_for", "class _Callbacks", "class _Stubs",
                        "apply_template_flags"):
        assert declaration in binder_source, declaration
    # The modules those two replaced are gone, not re-exported.
    for gone in ("interface_table", "plan", "surface", "schema", "stubs", "wrappers", "config",
                 "sessions", "baseline", "assembly", "universe"):
        assert not (ROOT / "engine" / f"{gone}.py").exists(), gone
    assert "Idl" in bindings_source
    assert "class Binder" not in bindings_source
    # The offline extractor writes documents, so the writer, the version it writes and the two
    # value vocabularies it states have to stay reachable there -- one statement, so what the
    # document and the engine agree on changes in one place.
    assert "from ..engine.idl import ArrayProto, DefinitionKind, IDL_VERSION, write_idl" in (
        extractor_source)


def test_bindings_stay_a_composition_root():
    """Stub, callback and binder bodies must not creep back into bindings.py."""
    bindings_source = (ROOT / "engine" / "bindings.py").read_text(encoding="utf-8")
    for token in (
        "_stub_method", "_member_configs", "_class_config_for", "_declared_handlers",
        "class _Stubs", "class _Callbacks", "class ClassConfigBuilder",
        "class BindingSession", "def _stub_", "mark_direct",
    ):
        assert token not in bindings_source, token


def test_binder_collaborators_use_public_surface_only():
    """The binder and a realm's session must not reach into bindings privates."""
    for path in (ROOT / "engine" / "binder.py", ROOT / "platform" / "sessions.py"):
        source = path.read_text(encoding="utf-8")
        assert "self.bindings._" not in source, path
        assert "bindings._binder" not in source, path


def test_layering_has_no_upward_imports():
    forbidden = {
        "foundation": ("engine", "network", "web", "platform"),
        "engine": ("web", "platform"),
        "network": ("engine", "web", "platform"),
        "web": ("platform",),
    }
    for layer, names in forbidden.items():
        for path in (ROOT / layer).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for name in names:
                assert f"pyv8c.{name}" not in source, path
                assert f"from ..{name}" not in source, path


@pytest.mark.native
def test_runtime_interface_catalog_is_frozen_after_bootstrap():
    pytest.importorskip("pyv8", reason="imports platform modules bound to the native extension")
    from pyv8c.platform.bootstrap import bootstrap_runtime

    runtime = bootstrap_runtime()
    assert "core" in runtime.loaded_features
    assert bootstrap_runtime() is not runtime


@pytest.mark.parametrize("attribute", ["_realm", "_services"])
@pytest.mark.parametrize("bundled", [False, True])
def test_realm_of_returns_the_realm_capability(attribute, bundled):
    from types import SimpleNamespace

    from pyv8c.foundation.realm import realm_of

    capability = object()
    services = SimpleNamespace(realm_services=capability) if bundled else capability
    subject = SimpleNamespace(**{attribute: services})
    assert realm_of(subject) is capability


def test_realm_of_prefers_injection_and_falls_back_to_services():
    from types import SimpleNamespace

    from pyv8c.foundation.realm import realm_of

    injected = object()
    fallback = object()
    subject = SimpleNamespace(_realm=injected, _services=SimpleNamespace(realm_services=fallback))
    assert realm_of(subject) is injected
    subject._realm = None
    assert realm_of(subject) is fallback
    assert realm_of(object()) is None


@pytest.mark.native
def test_page_exposes_narrow_realm_services():
    pytest.importorskip("pyv8", reason="constructs a live Browser")
    from pyv8c import Browser
    from pyv8c.foundation.realm import (
        ContextServices,
        DocumentServices,
        RealmServices,
        ResourceServices,
        TaskServices,
        WorkerServices,
    )

    browser = Browser(stub_logging="off", real_network=False)
    page = browser.new_context().new_page()
    try:
        assert isinstance(page.services.context_services, ContextServices)
        assert isinstance(page.services.realm_services, RealmServices)
        assert isinstance(page.services.task_services, TaskServices)
        assert isinstance(page.services.resource_services, ResourceServices)
        assert isinstance(page.services.document_services, DocumentServices)
        assert isinstance(page.services.worker_services, WorkerServices)
        for protocol in (ContextServices, RealmServices, TaskServices, ResourceServices,
                         DocumentServices, WorkerServices):
            assert not isinstance(page.services, protocol)
        assert page.services.context_services.network_available is False
        assert page.services.realm_services.interface_materialized("Document")
    finally:
        browser.close()


def test_foundation_and_services_have_no_owner_backdoors():
    """TaskQueue must not reach the Page (or any host) through an owner field."""
    tasks_source = (ROOT / "foundation" / "tasks.py").read_text(encoding="utf-8")
    assert "self.owner" not in tasks_source
    assert "TaskHost" in tasks_source

    services_source = (ROOT / "platform" / "services.py").read_text(encoding="utf-8")
    assert "_tasks.owner" not in services_source
    assert "_host.owner" not in services_source


def test_task_queue_exposes_event_loop_and_error_contract():
    from pyv8c.foundation.tasks import TaskQueue, TaskSource

    for member in ("run_turn", "next_deadline", "drain_task_errors", "pump_until"):
        assert hasattr(TaskQueue, member), member
    assert TaskSource.PARSER.value == "parser"


def test_domain_code_uses_page_resource_ownership_api():
    for path in (ROOT / "web").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in (
            "context_owner", "network_backend", "binding_session",
            "_owned_websockets", "_owned_workers", "_owned_frames",
            "binding_configuration(", "resource_loader",
            "self._page", "self.page",
        ):
            assert token not in source, (path, token)


@pytest.mark.native
def test_page_services_surface_is_structurally_narrow():
    """PageServices must not grow escape hatches into platform internals."""
    pytest.importorskip("pyv8", reason="constructs a live Browser")
    from pyv8c import Browser

    source = (ROOT / "platform" / "services.py").read_text(encoding="utf-8")
    for token in ("self.context_owner", "self.network_backend", "self.binding_session", "self.page"):
        assert token not in source, token

    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        services = page.services
        for name in ("context_owner", "network_backend", "binding_session", "page", "realm", "context", "browser"):
            assert not hasattr(services, name), name
    finally:
        browser.close()


@pytest.mark.native
def test_page_keeps_only_user_facing_api():
    """Web-serving forwards must not creep back onto the Page facade."""
    pytest.importorskip("pyv8", reason="imports platform modules bound to the native extension")
    from pyv8c.platform.browser import Page

    for name in (
        "post_task", "schedule", "cancel", "begin_network", "complete_network",
        "begin_worker", "complete_worker", "make_promise",
        "promise_resolve", "install_inline_handler", "load_resource",
        "load_resource_async", "load_iframe", "resolve_worker_source",
        "websocket", "close_websocket", "network_available", "cookie_jar",
        "context_services", "binding_services", "devtools_server",
        "worker_configuration", "create_worker_realm", "new_child_page",
        "materialize_interface", "interface_materialized", "request_navigation",
        "element_class", "_node_adopted", "_parser_script_start",
    ):
        assert not hasattr(Page, name), name


@pytest.mark.native
def test_page_net_exposes_both_http_flavours():
    """The host transport and the page's own JS APIs are one surface, under ``net``."""
    pytest.importorskip("pyv8", reason="imports platform modules bound to the native extension")
    from pyv8c.platform.net import PageNet
    from pyv8c.platform.page import Page

    for name in ("get", "post", "fetch", "fetch_async", "xhr", "xhr_async"):
        assert hasattr(PageNet, name), name
    for name in ("fetch", "fetch_async", "xhr", "xhr_async"):
        assert not hasattr(Page, name), f"{name} belongs to page.net"


def test_web_capability_split_has_no_legacy_mixed_modules():
    assert not (ROOT / "web" / "fetch" / "backend.py").exists()
    assert not (ROOT / "web" / "fetch" / "api.py").exists()
    # The HTTP and socket engines are assembled per context; the module that
    # used to answer one role's gap from the other role's engine is gone.
    assert not (ROOT / "network" / "composite.py").exists()
    assert "class CookieJar" in (ROOT / "network" / "cookies.py").read_text(encoding="utf-8")
    assert "class CronetTransport" in (ROOT / "network" / "cronet.py").read_text(encoding="utf-8")
    assert "class NetworkBackend" in (ROOT / "network" / "backend.py").read_text(encoding="utf-8")
    assert "def assemble_backend" in (ROOT / "network" / "assembly.py").read_text(encoding="utf-8")
    assert "class WebSocketsTransport" in (ROOT / "network" / "websockets.py").read_text(encoding="utf-8")
    assert "class WorkerRuntime" in (ROOT / "web" / "workers" / "runtime.py").read_text(encoding="utf-8")
    # The media classes own their module; the tag classes keep theirs.
    assert "class HTMLMediaElement" in (ROOT / "web" / "dom" / "html_media.py").read_text(encoding="utf-8")
    assert "class HTMLMediaElement" not in (ROOT / "web" / "dom" / "html_elements.py").read_text(encoding="utf-8")


def test_the_two_network_roles_stay_separate_contracts():
    """Each role is what an engine must implement to fill it, and no more."""
    from pyv8c.network.registry import HttpTransport, WebSocketTransport

    assert not hasattr(HttpTransport, "websocket")
    assert not hasattr(WebSocketTransport, "request_sync")
