import copy
import json
from pathlib import Path

import pytest

from pyv8c.engine.idl import DefinitionKind, Idl
from pyv8c.tools import extract_idl
from pyv8c.tools.extract_idl import (
    _constant_definitions,
    _idl_handler_findings,
    _idl_property_families,
    _property_mask,
    extract_constants,
    extract_template,
    write_idl,
)


def _bundled_baseline_document():
    idl = Idl.load(profile=None)
    return {"idl": dict(idl.metadata), "interfaces": copy.deepcopy(dict(idl.interfaces))}


def test_balanced_binding_tables_extract_all_locations_and_descriptors():
    text = r'''
const WrapperTypeInfo V8Fixture::wrapper_type_info_{
  gin::kEmbedderBlink, V8Fixture::InstallInterfaceTemplate, nullptr,
  "Fixture", V8EventTarget::GetWrapperTypeInfo(), 1, 2,
};
void GetterCallback(const v8::FunctionCallbackInfo<v8::Value>& info) {}
void SetterCallback(const v8::FunctionCallbackInfo<v8::Value>& info) {
  auto&& value = NativeValueTraits<IDLBoolean>::ArgumentValue(isolate, 0, info[0], exception_state);
}
void RunOperationCallback(const v8::FunctionCallbackInfo<v8::Value>& info) {
  if (UNLIKELY(info.Length() < 1)) { exception_state.ThrowTypeError(ExceptionMessages::NotEnoughArguments(1, info.Length())); }
  auto&& value = NativeValueTraits<IDLString>::ArgumentValue(isolate, 0, info[0], exception_state);
  if (foo({1, 2})) { bar(); }
}
static const IDLMemberInstaller::AttributeConfig kAttributeTable[] = {
  {"readonly", GetterCallback, nullptr, unsigned(v8::None), unsigned(IDLMemberInstaller::FlagLocation::kPrototype), unsigned(IDLMemberInstaller::FlagWorld::kAllWorlds), unsigned(IDLMemberInstaller::FlagReceiverCheck::kCheck), unsigned(IDLMemberInstaller::FlagCrossOriginCheck::kCheck), unsigned(v8::SideEffectType::kHasNoSideEffect), unsigned(V8PrivateProperty::CachedAccessor::kNone)},
  {"value", GetterCallback, SetterCallback, unsigned(v8::None), unsigned(IDLMemberInstaller::FlagLocation::kInstance), unsigned(IDLMemberInstaller::FlagWorld::kAllWorlds), unsigned(IDLMemberInstaller::FlagReceiverCheck::kCheck), unsigned(IDLMemberInstaller::FlagCrossOriginCheck::kDoNotCheck), unsigned(v8::SideEffectType::kHasSideEffect), unsigned(V8PrivateProperty::CachedAccessor::kNone)},
};
static const IDLMemberInstaller::OperationConfig kOperationTable[] = {
  {"run", RunOperationCallback, 1, unsigned(v8::None), unsigned(IDLMemberInstaller::FlagLocation::kInterface), unsigned(IDLMemberInstaller::FlagWorld::kAllWorlds), unsigned(IDLMemberInstaller::FlagReceiverCheck::kDoNotCheck), unsigned(IDLMemberInstaller::FlagCrossOriginCheck::kDoNotCheck), unsigned(v8::SideEffectType::kHasSideEffect)},
};
static const IDLMemberInstaller::ConstantValueConfig kConstantValueTable[] = {
  {"ANSWER", Fixture::Constant::kAnswer},
};
'''
    members = extract_idl.extract_members(text)
    assert [item["name"] for item in members["prototype"]] == ["readonly"]
    assert members["instance"][0]["setter"] == "SetterCallback"
    assert members["instance"][0]["setter_signature"]["parameters"][0]["idl_type"] == "IDLBoolean"
    assert members["interface"][0]["name"] == "run"
    assert members["interface"][0]["signature"]["required"] == 1
    assert members["interface"][0]["signature"]["parameters"][0]["idl_type"] == "IDLString"
    assert members["interface"][1]["kind"] == "constant"


def test_generated_constant_expression_resolves_from_header(tmp_path):
    header = tmp_path / "v8_gpu_buffer_usage.h"
    header.write_text(
        "class V8GPUBufferUsage { public: class Constant { public: "
        "static constexpr uint32_t kMapRead = 1; "
        "static constexpr uint32_t kCopySrc = 1 << 2; }; };",
        encoding="utf-8",
    )
    members = {"instance": [], "prototype": [], "interface": [{
        "name": "COPY_SRC", "kind": "constant",
        "value": "V8GPUBufferUsage::Constant::kCopySrc",
    }]}
    definitions = _constant_definitions(tmp_path)
    constants = extract_constants(members, definitions)
    assert constants[0]["resolved"] is True
    assert constants[0]["value"] == 4


def test_a_constant_keeps_its_value_and_not_its_expression():
    extracted = {
        "Fixture": {
            "sources": [], "constructor": {}, "template": {},
            "members": {"instance": [], "prototype": [], "interface": [{
                "name": "COPY_SRC", "kind": "constant", "value": "1 << 2",
            }]},
            "constants": [{"name": "COPY_SRC", "expression": "1 << 2", "resolved": True, "value": 4}],
        },
    }
    compact = extract_idl.idl_interfaces(extracted)["Fixture"]
    assert compact["constants"] == [{"name": "COPY_SRC", "resolved": True, "value": 4, "location": "interface"}]


def test_idl_metadata_reads_star_exposure_and_callback_interfaces(tmp_path):
    (tmp_path / "streams.idl").write_text(
        "[\n    Exposed=*\n] interface ReadableStream {\n};\n", encoding="utf-8")
    (tmp_path / "node_filter.idl").write_text(
        "[\n    Exposed=Window\n] callback interface NodeFilter {\n};\n", encoding="utf-8")
    (tmp_path / "url.idl").write_text(
        "[\n    Exposed=Window,\n    LegacyWindowAlias=webkitURL\n] interface URL {\n};\n",
        encoding="utf-8")
    metadata = extract_idl._idl_metadata(tmp_path)
    assert metadata["ReadableStream"]["exposed_in"] == ["Window", "Worker"]
    assert metadata["NodeFilter"]["exposed_in"] == ["Window"]
    assert metadata["URL"]["aliases"] == ["webkitURL"]


def test_the_definition_kind_decides_the_interface_object():
    """One word, three kinds: a namespace has no prototype either, as ``CSS`` shows."""
    namespace = extract_idl.extract_template(
        "WrapperTypeInfo::kWrapperTypeNoPrototype,\nWrapperTypeInfo::kIdlNamespace,\n")
    callback = extract_idl.extract_template(
        "WrapperTypeInfo::kWrapperTypeNoPrototype,\nWrapperTypeInfo::kIdlInterface,\n")
    ordinary = extract_idl.extract_template("WrapperTypeInfo::kIdlInterface,\n")

    assert namespace["definition_kind"] == DefinitionKind.kIdlNamespace
    assert callback["definition_kind"] == DefinitionKind.kWrapperTypeNoPrototype
    #: An ordinary interface states nothing: the fragment of a split binding that carries no
    #: ``WrapperTypeInfo`` (``CSS`` is declared in one file and implemented in another) then
    #: cannot erase the kind the other fragment declares.
    assert ordinary["definition_kind"] is None


def test_template_handler_comments_are_not_callback_names():
    text = r'''
void NamedGetterCallback() {}
void NamedSetterCallback() {}
void NamedQueryCallback() {}
void NamedDeleterCallback() {}
void NamedEnumeratorCallback() {}
void NamedDefinerCallback() {}
void IndexedGetterCallback() {}
void IndexedSetterCallback() {}
void IndexedQueryCallback() {}
void IndexedDeleterCallback() {}
void IndexedEnumeratorCallback() {}
void IndexedDefinerCallback() {}
void Install() {
  NamedPropertyHandlerConfiguration(
      NamedGetterCallback,
      NamedSetterCallback,
      // query
      NamedQueryCallback,
      // deleter
      NamedDeleterCallback,
      // enumerator
      NamedEnumeratorCallback,
      // definer
      NamedDefinerCallback);
  IndexedPropertyHandlerConfiguration(
      IndexedGetterCallback, IndexedSetterCallback, /* query */ IndexedQueryCallback,
      IndexedDeleterCallback, IndexedEnumeratorCallback, IndexedDefinerCallback);
}
'''
    handlers = extract_template(text)["handlers"]
    assert handlers["named"] == {
        "get": "NamedGetterCallback", "set": "NamedSetterCallback",
        "query": "NamedQueryCallback", "del": "NamedDeleterCallback",
        "enum": "NamedEnumeratorCallback", "definer": "NamedDefinerCallback",
    }
    assert handlers["indexed"] == {
        "get": "IndexedGetterCallback", "set": "IndexedSetterCallback",
        "query": "IndexedQueryCallback", "del": "IndexedDeleterCallback",
        "enum": "IndexedEnumeratorCallback", "definer": "IndexedDefinerCallback",
    }


def test_cross_origin_configurations_are_not_the_interfaces_own_handlers():
    """Cross-origin setups build their own configurations, inside the access check.

    Reading those as the interface's own handlers is what gave a global named
    handlers its IDL never declares.
    """
    text = r'''
void Install(InstanceTemplate* instance_template) {
  v8::NamedPropertyHandlerConfiguration(
      V8Widget::NamedPropertyGetterCallback,
      V8Widget::NamedPropertySetterCallback,
      nullptr,
      nullptr,
      nullptr,
      nullptr,
      v8::Local<v8::Value>(),
      v8::PropertyHandlerFlags::kNone);
  instance_template->SetAccessCheckCallbackAndHandler(
      CrossOriginAccessCheckCallback,
      v8::NamedPropertyHandlerConfiguration(
          CrossOriginNamedGetterCallback,
          CrossOriginNamedSetterCallback,
          CrossOriginNamedQueryCallback,
          CrossOriginNamedDeleterCallback,
          CrossOriginNamedEnumeratorCallback,
          CrossOriginNamedDefinerCallback,
          v8::Local<v8::Value>(),
          v8::PropertyHandlerFlags::kNone),
      v8::IndexedPropertyHandlerConfiguration(
          CrossOriginIndexedGetterCallback,
          nullptr,
          nullptr,
          nullptr,
          nullptr,
          nullptr,
          v8::Local<v8::Value>(),
          v8::PropertyHandlerFlags::kNone));
}
'''
    handlers = extract_template(text)["handlers"]
    assert handlers["named"] == {
        "get": "V8Widget::NamedPropertyGetterCallback",
        "set": "V8Widget::NamedPropertySetterCallback",
    }
    assert handlers["indexed"] == {}
    assert handlers["cross_origin"]["named"] == {
        "get": "CrossOriginNamedGetterCallback", "set": "CrossOriginNamedSetterCallback",
        "query": "CrossOriginNamedQueryCallback", "del": "CrossOriginNamedDeleterCallback",
        "enum": "CrossOriginNamedEnumeratorCallback", "definer": "CrossOriginNamedDefinerCallback",
    }
    assert handlers["cross_origin"]["indexed"] == {"get": "CrossOriginIndexedGetterCallback"}


def test_a_named_handler_needs_a_named_getter_in_the_idl():
    """The IDL decides which families a set of generated handlers can belong to."""
    named = _idl_property_families("  [NotEnumerable, CrossOrigin] getter object (DOMString name);\n")
    indexed = _idl_property_families("  getter Widget (unsigned long index);\n")
    assert named == {"named": {"get": True}, "indexed": {}}
    assert indexed == {"named": {}, "indexed": {"get": True}}

    metadata = {"Window": {"properties": indexed}, "Storage": {"properties": named}}
    extracted = {
        "Window": {"template": {"handlers": {"named": {"get": "callback"}}}},
        "Storage": {"template": {"handlers": {"named": {"get": "callback"}}}},
    }
    assert _idl_handler_findings(extracted, metadata) == [{
        "interface": "Window",
        "family": "named",
        "handlers": ["get"],
        "reason": "no named getter or setter in the interface's IDL",
    }]


def test_the_document_states_the_chromium_release_it_was_read_from():
    """The release comes from the checkout, not from the GN output directory or the fetch target."""
    metadata = _bundled_baseline_document()["idl"]

    assert "renderer_snapshot" not in metadata
    assert extract_idl.VERSION_PATTERN.fullmatch(metadata["chromium_version"])


def test_the_chromium_version_is_read_from_the_checkout(tmp_path):
    version_file = tmp_path / "src" / "chrome" / "VERSION"
    version_file.parent.mkdir(parents=True)
    version_file.write_text("MAJOR=154\nMINOR=0\nBUILD=8030\nPATCH=0\n", encoding="ascii")

    assert extract_idl.read_chromium_version(tmp_path) == "154.0.8030.0"


def test_a_checkout_that_states_no_version_is_refused_by_name(tmp_path):
    with pytest.raises(ValueError, match="VERSION"):
        extract_idl.read_chromium_version(tmp_path)


def test_real_renderer_sources_have_members_for_core_interfaces():
    root = Path(__file__).resolve().parents[3] / "_pyv8env" / "renderer"
    if not root.is_dir():
        pytest.skip(f"no generated Chromium sources at {root}")
    snapshot = extract_idl.extract(root)
    for name in ("Event", "EventTarget", "Node", "Document", "Element", "Window", "Headers", "Blob", "Request", "Response", "URL"):
        entry = snapshot[name]
        assert any(entry["members"].values()), name
    assert len(snapshot["URL"]["sources"]) >= 2
    assert snapshot["_report"]["interfaces"] > 1000
    assert snapshot["_report"]["unresolved_signatures"] > 0


def test_generated_baseline_keeps_full_runtime_snapshot_and_report():
    baseline = _bundled_baseline_document()
    metadata = baseline["idl"]
    assert metadata["version"] == extract_idl.IDL_VERSION
    assert metadata["report"]["interfaces"] > 1000
    assert metadata["report"]["unresolved_signatures"] > 0
    interfaces = baseline["interfaces"]
    assert all("callbacks" not in value for value in interfaces.values())
    assert len(interfaces["Event"]["members"]["prototype"]) >= 5
    assert len(interfaces["Node"]["members"]["prototype"]) > 10
    assert len(interfaces["URL"]["members"]["interface"]) >= 2
    assert interfaces["HTMLAllCollection"]["template"]["undetectable"] is True
    handlers = interfaces["WindowProperties"]["template"]["handlers"]
    assert handlers["named"]["del"] == 1
    assert handlers["named"]["definer"] == 1
    assert handlers["indexed"]["del"] == 1
    assert handlers["indexed"]["definer"] == 1
    entry = interfaces["URL"]
    assert set(entry) == {"aliases", "constants", "constructor", "exposed_in", "members", "parent", "tag", "template"}
    assert "provenance" not in entry
    assert "handlers" in entry["template"]
    assert all(member["kind"] != "constant" for values in entry["members"].values() for member in values)


def test_write_idl_writes_one_json_file(tmp_path):
    document = {"idl": {"version": 7}, "interfaces": {"Fixture": {"parent": None}}}
    written = write_idl(tmp_path / "idl.json", document)
    assert written.name == "idl.json"
    assert json.loads(written.read_text(encoding="utf-8")) == document


def test_a_document_states_only_what_a_binding_reads():
    extracted = {
        "Fixture": {
            "parent": None,
            "sources": [],
            "constructor": {"callback": "ConstructorCallback"},
            "template": {
                "call_handler": "CallHandler",
                "access_check": {"callback": "AccessCheck", "with_handler": True},
                "handlers": {"named": {"get": "NamedGetterCallback"}, "indexed": {},
                             "cross_origin": {"named": {}, "indexed": {}}},
            },
            "members": {"instance": [], "prototype": [{
                "name": "run", "kind": "operation", "callback": "RunCallback",
                "world": "AllWorlds", "length": 0, "signature": {"parameters": []},
            }, {
                "name": "value", "kind": "attribute", "getter": "GetCallback",
                "setter": "SetCallback", "world": "AllWorlds",
            }], "interface": []},
            "constants": [],
        },
    }
    compact = extract_idl.idl_interfaces(extracted)["Fixture"]
    assert "callback" not in compact["constructor"]
    assert compact["template"]["call_handler"] == 1
    assert compact["template"]["access_check"] == {"with_handler": True, "enabled": 1}
    assert compact["template"]["handlers"]["named"]["get"] == 1
    operation, attribute = compact["members"]["prototype"]
    # Only what the runtime reads survives: the world's name becomes the one bit it needs.
    assert "callback" not in operation and "signature" not in operation
    assert operation["main_world"] is True and "world" not in operation and "raw" not in operation
    assert "getter" not in attribute and "readonly" not in attribute and attribute["setter"] == 1
    assert "signature" not in compact["constructor"]


def test_a_member_of_another_world_states_its_bit_as_false():
    """A runtime baseline says which world a member belongs to, not the world's name."""
    extracted = {"Fixture": {"members": {"prototype": [
        {"name": "run", "kind": "operation", "world": "IsolatedWorld", "length": 0},
    ]}}}
    compact = extract_idl.idl_interfaces(extracted)["Fixture"]
    assert compact["members"]["prototype"][0]["main_world"] is False


def test_no_alloc_direct_call_tables_keep_their_operations():
    """Chromium emits the fast-path methods into a table of their own, whose entries carry
    one more brace level and a trailing ``v8::CFunction`` reference."""
    text = r'''
static const IDLMemberInstaller::NoAllocDirectCallOperationConfig kOperationTable[] = {
  {{"bindBuffer", BindBufferOperationCallback, 2, unsigned(v8::None), unsigned(IDLMemberInstaller::FlagLocation::kPrototype), unsigned(IDLMemberInstaller::FlagWorld::kAllWorlds), unsigned(IDLMemberInstaller::FlagReceiverCheck::kCheck), unsigned(IDLMemberInstaller::FlagCrossOriginCheck::kCheck), unsigned(v8::SideEffectType::kHasSideEffect)}, kNoAllocDirectCallOverloadsOfBindBuffer},
  {{"viewport", ViewportOperationCallback, 4, unsigned(v8::None), unsigned(IDLMemberInstaller::FlagLocation::kPrototype), unsigned(IDLMemberInstaller::FlagWorld::kAllWorlds), unsigned(IDLMemberInstaller::FlagReceiverCheck::kCheck), unsigned(IDLMemberInstaller::FlagCrossOriginCheck::kCheck), unsigned(v8::SideEffectType::kHasSideEffect)}, kNoAllocDirectCallOverloadsOfViewport},
};
void BindBufferOperationCallback(const v8::FunctionCallbackInfo<v8::Value>& info) {
  if (UNLIKELY(info.Length() < 2)) { exception_state.ThrowTypeError(ExceptionMessages::NotEnoughArguments(2, info.Length())); }
  auto&& target = NativeValueTraits<IDLUnsignedLong>::ArgumentValue(isolate, 0, info[0], exception_state);
}
'''
    members = extract_idl.extract_members(text)
    assert [item["name"] for item in members["prototype"]] == ["bindBuffer", "viewport"]
    assert members["prototype"][0]["kind"] == "operation"
    assert members["prototype"][0]["length"] == 2
    assert members["prototype"][0]["location"] == "prototype"
    assert members["prototype"][0]["signature"]["required"] == 2
    assert members["prototype"][1]["length"] == 4
