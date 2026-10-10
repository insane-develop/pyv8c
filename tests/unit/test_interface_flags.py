"""IdlMember property flags arrive as the bitmask the binding uses, not only as text.

The flags are the document's: a class states one only where it disagrees, and the side-effect
flags are the ones a page can feel, because V8 may elide a call it believes has none.
"""
import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8.flag import ArrayProto, DefinitionKind, SideEffectType, V8Attribute  # noqa: E402
from pyv8c.engine.binder import property_flags  # noqa: E402
from pyv8c.engine.idl import ARRAY_PROTOS, DEFINITION_KINDS, Idl  # noqa: E402
from pyv8c.tools.extract_idl import _property_mask  # noqa: E402


def test_the_documents_vocabulary_is_the_bindings():
    """Two spellings of the same numbers, so a renumbering on either side is loud.

    A document states ``template.array`` and ``template.definition_kind`` as ints and the engine
    restates the values because reading one must not need the native extension; ``pyv8.flag``
    names them for a caller.  This is the guard that keeps the three equal.
    """
    assert ARRAY_PROTOS == (ArrayProto.iterator, ArrayProto.iterable)
    assert DEFINITION_KINDS == (DefinitionKind.kInterface, DefinitionKind.kIdlNamespace,
                                DefinitionKind.kWrapperTypeNoPrototype)


def test_the_extractor_reads_a_table_flags_field_as_a_mask():
    assert _property_mask("unsigned(v8::None)") == V8Attribute.kNone
    assert _property_mask("unsigned(v8::ReadOnly) | unsigned(v8::DontEnum)") == (
        V8Attribute.ReadOnly | V8Attribute.DontEnum
    )
    assert _property_mask("somethingUnreadable") is None


def _member_entry(**fields):
    """A one-member document the collection decodes, so the flags come from a real IdlMember."""
    member = {"name": "value", "kind": "attribute", **fields}
    entry = {"members": {"instance": [member]}}
    idl = Idl(path="test", digest="", metadata={}, interfaces={"Widget": entry})
    return idl.member("Widget", "value", "attribute")


def test_the_table_prefers_a_stated_mask_and_still_reads_the_older_text():
    assert property_flags(_member_entry(property_flags=6)) == 6
    assert property_flags(_member_entry(flags="v8::DontEnum | v8::DontDelete")) == (
        V8Attribute.DontEnum | V8Attribute.DontDelete
    )


def test_the_compact_snapshot_drops_the_flag_text_only_when_the_mask_is_there():
    from pyv8c.tools.extract_idl import _compact_member_callback_metadata

    read = {"name": "value", "property_flags": 2, "flags": "v8::DontEnum"}
    unread = {"name": "value", "property_flags": None, "flags": "v8::DontEnum"}
    _compact_member_callback_metadata(read)
    _compact_member_callback_metadata(unread)
    assert read == {"name": "value", "property_flags": 2, "main_world": True}
    assert unread["flags"] == "v8::DontEnum"


#: Handler attributes that used to state they had no side effect where the document says they
#: have one.  The declarations are gone, so this holds the document's answer in place.
SIDE_EFFECTS = (
    ("AudioContext", "onerror"),
    ("AudioScheduledSourceNode", "onended"),
    ("OfflineAudioContext", "oncomplete"),
)


@pytest.fixture(scope="module")
def bindings():
    from pyv8c.engine.bindings import Bindings
    from pyv8c.platform.bootstrap import bootstrap_runtime

    return Bindings(Idl.load(), runtime=bootstrap_runtime(), stub_logging="off")


@pytest.mark.parametrize("interface, member", SIDE_EFFECTS)
def test_a_handler_reads_the_documents_answer_about_side_effects(bindings, interface, member):
    compiled = {attribute.name: attribute
                for attribute in bindings.class_config_for(interface).attributes}[member]
    stated = bindings.idl.member(interface, member, "attribute")

    assert compiled.side_effect == (stated.side_effect or SideEffectType.kHasSideEffect)

