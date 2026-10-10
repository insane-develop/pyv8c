"""Extract an interface snapshot from generated Blink bindings."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
from pathlib import Path

from ..engine.idl import ArrayProto, DefinitionKind, IDL_VERSION, write_idl

INTERFACE_DECL = re.compile(r"const\s+WrapperTypeInfo\s+V8(?P<binding>[A-Za-z0-9_]+)::wrapper_type_info_\s*\{")
FUNCTION_DECL = re.compile(r"(?:void|v8::Local\s*<\s*v8::Value\s*>)\s+(?P<name>[A-Za-z0-9_]+Callback)\s*\([^)]*\)\s*\{")
TABLE_DECL = re.compile(
    r"static\s+const\s+IDLMemberInstaller::"
    r"(?P<kind>Attribute|Operation|ConstantValue|NoAllocDirectCallOperation)Config\s+"
    r"(?P<name>k[A-Za-z0-9_]+Table)\s*\[\]\s*=\s*\{")
#: Table kinds that hold operations under another name: Chromium emits the no-alloc
#: direct-call fast path into its own table, whose entries add a brace level around the
#: operation fields and a trailing ``v8::CFunction`` reference.
OPERATION_TABLE_KINDS = {"NoAllocDirectCallOperation": "Operation"}
#: A constructor callback, however the code generator names it: the older shape spells
#: it ``URLConstructorCallback`` after the interface, the newer one emits a free
#: ``ConstructorCallback`` inside the binding's own namespace and installs it with
#: ``SetCallHandler`` instead of a constructor table.
CONSTRUCTOR_CALLBACK = re.compile(r"\b[A-Za-z0-9_]*ConstructorCallback\b")
IDL_INTERFACE_DECL = re.compile(
    r"(?:(?P<attrs>\[[\s\S]*?\])\s*)?(?P<partial>\bpartial\s+)?(?:callback\s+)?interface(?:\s+mixin)?\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*(?P<parent>[A-Za-z_][A-Za-z0-9_]*))?\s*\{")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHROMIUM_ROOT = Path(
    os.environ.get("CHROMIUM_ROOT", PROJECT_ROOT / ".chromium-build" / "chromium")
)

#: The four parts a checkout states in ``src/chrome/VERSION``, e.g. ``154.0.8030.0``.
VERSION_KEYS = ("MAJOR", "MINOR", "BUILD", "PATCH")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+")


def read_chromium_version(root: Path) -> str:
    """The release the checkout states, or a loud refusal naming what to read instead.

    The version is read from ``<root>/src/chrome/VERSION``, not from the GN output directory
    (``out/Cronet145`` says nothing about the release) and not from the fetch target: a
    document has to state the release its own bytes came from.
    """
    version_file = Path(root) / "src" / "chrome" / "VERSION"
    try:
        text = version_file.read_text(encoding="ascii")
    except OSError as exc:
        raise ValueError(
            f"cannot read the Chromium version from {version_file} ({exc}); pass"
            " --chromium-version, or point --chromium-root at the checkout the bindings"
            " were generated from") from exc
    stated = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    parts = [(stated.get(key) or "").strip() for key in VERSION_KEYS]
    if not all(part.isdigit() for part in parts):
        raise ValueError(f"{version_file} does not state {'/'.join(VERSION_KEYS)}: {text.strip()!r}")
    return ".".join(parts)


def _skip_string(text, index):
    quote = text[index]
    index += 1
    while index < len(text):
        if text[index] == "\\": index += 2
        elif text[index] == quote: return index + 1
        else: index += 1
    return index


def balanced_end(text, start, opening="{", closing="}"):
    if start >= len(text) or text[start] != opening:
        raise ValueError("unbalanced block")
    depth = 0; index = start
    while index < len(text):
        char = text[index]
        if char in ('"', "'"):
            index = _skip_string(text, index); continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2); index = len(text) if newline < 0 else newline + 1; continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2); index = len(text) if end < 0 else end + 2; continue
        if char == opening: depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0: return index + 1
        index += 1
    raise ValueError("unterminated block")


def split_top_level(text, separator=","):
    result, start, stack, index = [], 0, [], 0
    while index < len(text):
        char = text[index]
        if char in ('"', "'"):
            index = _skip_string(text, index); continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2); index = len(text) if newline < 0 else newline + 1; continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2); index = len(text) if end < 0 else end + 2; continue
        if char in "({[<": stack.append(char)
        elif char in "}])>" and stack: stack.pop()
        elif char == separator and not stack:
            result.append(text[start:index].strip()); start = index + 1
        index += 1
    result.append(text[start:].strip())
    return result


def _clean_callback_reference(value):
    """Normalize a generated handler argument to its callback symbol.

    Blink annotates handler tuple entries with comments such as ``// query``; those
    must not become part of the callback name in the baseline.
    """
    value = re.sub(r"//[^\r\n]*", "", value)
    value = re.sub(r"/\*[\s\S]*?\*/", "", value)
    return value.strip()


def _strip_unsigned(value):
    value = value.strip()
    value = re.sub(r"^unsigned\s*\(\s*", "", value)
    return re.sub(r"\s*\)$", "", value).strip()

_V8_ATTRIBUTES = {"None": 0, "ReadOnly": 1, "DontWrite": 1, "DontEnum": 2, "DontDelete": 4}


def _property_mask(value):
    """The v8 attribute bitmask a table's flags field spells, or None when unreadable."""
    words = re.findall(r"v8::([A-Za-z]+)", _strip_unsigned(value))
    if not words or any(word not in _V8_ATTRIBUTES for word in words):
        return None
    mask = 0
    for word in words:
        mask |= _V8_ATTRIBUTES[word]
    return mask


def _flag(value, prefix):
    match = re.search(re.escape(prefix) + r"::k([A-Za-z0-9_]+)", _strip_unsigned(value))
    return match.group(1) if match else None


def _table_entries(text, table_start):
    end = balanced_end(text, table_start); body = text[table_start + 1:end - 1]; entries = []; index = 0
    while index < len(body):
        if body[index] == "{":
            entry_end = balanced_end(body, index); fields = split_top_level(body[index + 1:entry_end - 1])
            if fields and fields[0].startswith("{"):
                fields = split_top_level(_unwrap(fields[0]))
            if fields and fields[0].startswith('"'): entries.append(fields)
            index = entry_end
        else: index += 1
    return entries


def _unwrap(value):
    """The fields inside an extra brace level, as a direct-call table writes them."""
    value = value.strip()
    return value[1:-1] if value.startswith("{") and value.endswith("}") else value


def _find_functions(text):
    functions = {}
    for match in FUNCTION_DECL.finditer(text):
        try: end = balanced_end(text, text.find("{", match.start()))
        except ValueError: continue
        functions[match.group("name")] = text[match.end():end - 1]
    return functions


def _argument_types(body):
    result = {}
    trait = re.compile(r"NativeValueTraits\s*<")
    for match in trait.finditer(body):
        depth, index = 1, match.end()
        while index < len(body) and depth:
            if body[index] == "<": depth += 1
            elif body[index] == ">": depth -= 1
            index += 1
        if depth: continue
        type_name = body[match.end():index - 1].strip(); conversion = body[index:index + 80]
        if not re.match(r"::(?:ArgumentValue|NativeValue)", conversion): continue
        info = re.search(r"info\s*\[\s*(\d+)\s*\]", body[index:index + 700])
        if not info: continue
        argument_index = int(info.group(1)); parameter = result.setdefault(argument_index, {
            "index": argument_index, "idl_type": type_name, "optional": False,
            "nullable": False,
        })
        before = body[max(0, match.start() - 180):match.start()]; after = body[index:min(len(body), index + 500)]
        parameter["optional"] |= "IsUndefined" in before or "IsUndefined" in after
        parameter["optional"] |= type_name.startswith("IDLOptional<")
        parameter["nullable"] |= "Nullable" in type_name or "IsNull" in before or "IsNull" in after
        default = re.search(r"\{\s*([^{};]+)\s*\}", after)
        if default and parameter["optional"]: parameter["default"] = default.group(1).strip()
    return [result[key] for key in sorted(result)]


def extract_callback_signatures(text):
    signatures = {}
    for name, body in _find_functions(text).items():
        required = re.search(r"NotEnoughArguments\s*\(\s*(\d+)", body)
        parameters = _argument_types(body)
        signatures[name] = {
            "required": int(required.group(1)) if required else 0,
            "parameters": parameters,
            "status": "generated",
            "source_evidence": "NotEnoughArguments/NativeValueTraits in generated callback",
        }
    return signatures


def _member_from_table(kind, fields, signatures):
    if len(fields) < 2: return None
    name = fields[0].strip().strip('"')
    if kind == "Attribute":
        if len(fields) < 8: return {"name": name, "kind": "attribute", "review": "manual-review-required", "raw": fields}
        location = _flag(fields[4], "IDLMemberInstaller::FlagLocation") or "Unknown"
        setter = None if fields[2].strip() == "nullptr" else fields[2].strip()
        result = {"name": name, "kind": "attribute", "getter": fields[1].strip(),
                "setter": setter,
                "readonly": fields[2].strip() == "nullptr", "location": location.lower(),
                "flags": _strip_unsigned(fields[3]), "property_flags": _property_mask(fields[3]),
                "world": _flag(fields[5], "IDLMemberInstaller::FlagWorld"),
                "receiver": _flag(fields[6], "IDLMemberInstaller::FlagReceiverCheck"),
                "cross_origin": _flag(fields[7], "IDLMemberInstaller::FlagCrossOriginCheck"),
                "side_effect": _flag(fields[8], "v8::SideEffectType") if len(fields) > 8 else None,
                "cached_accessor": _flag(fields[9], "V8PrivateProperty::CachedAccessor") if len(fields) > 9 else None}
        if setter:
            result["setter_signature"] = signatures.get(setter, {
                "required": 1, "parameters": [], "status": "unresolved",
                "source_evidence": "attribute table without parseable setter callback",
            })
            if not result["setter_signature"].get("parameters"):
                result["setter_signature"]["status"] = "unresolved"
        return result
    if kind == "Operation":
        if len(fields) < 6: return {"name": name, "kind": "operation", "review": "manual-review-required", "raw": fields}
        callback, length = fields[1].strip(), int(fields[2].strip()); location = _flag(fields[4], "IDLMemberInstaller::FlagLocation") or "Unknown"
        signature = dict(signatures.get(callback, {
            "required": length, "parameters": [], "status": "unresolved",
            "source_evidence": "operation table without parseable callback",
        }))
        if length and len(signature.get("parameters") or ()) < length:
            signature["required"] = length
            signature["status"] = "unresolved"
        return {"name": name, "kind": "operation", "callback": callback, "length": length, "location": location.lower(),
                "flags": _strip_unsigned(fields[3]), "property_flags": _property_mask(fields[3]),
                "world": _flag(fields[5], "IDLMemberInstaller::FlagWorld"),
                "receiver": _flag(fields[6], "IDLMemberInstaller::FlagReceiverCheck") if len(fields) > 6 else None,
                "cross_origin": _flag(fields[7], "IDLMemberInstaller::FlagCrossOriginCheck") if len(fields) > 7 else None,
                "side_effect": _flag(fields[8], "v8::SideEffectType") if len(fields) > 8 else None,
                "signature": signature}
    return {"name": name, "kind": "constant", "value": fields[1].strip(), "location": "interface", "status": "unresolved"}


def extract_members(text):
    signatures = extract_callback_signatures(text); members = {"instance": [], "prototype": [], "interface": []}
    for match in TABLE_DECL.finditer(text):
        try: entries = _table_entries(text, text.find("{", match.start()))
        except ValueError: continue
        kind = OPERATION_TABLE_KINDS.get(match.group("kind"), match.group("kind"))
        for fields in entries:
            member = _member_from_table(kind, fields, signatures)
            if member is not None: members.setdefault(member.get("location", "interface"), []).append(member)
    return members


def _primitive_constant(expression):
    """Return a JSON-safe literal when Chromium emitted one, else None."""
    value = expression.strip()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return None
    if re.fullmatch(r"[-+]?0[xX][0-9A-Fa-f]+", value):
        try:
            return int(value, 0)
        except ValueError:
            return None
    if value in ("true", "false"):
        return value == "true"
    match = re.fullmatch(r'"((?:\\.|[^"\\])*)"', value)
    if match:
        try:
            return json.loads(value)
        except Exception:  # a value that is not JSON falls back to the raw match
            return match.group(1)
    return None


def _constant_expression_value(expression, definitions, resolving=()):
    """Evaluate the small, numeric C++ expression subset used by generated constants."""
    value = _primitive_constant(expression)
    if value is not None:
        return value
    expression = str(expression).strip()
    # Remove harmless C++ integer suffixes and the common cast wrappers.
    expression = re.sub(r"\b(0[xX][0-9A-Fa-f]+|\d+)(?:ull|uLL|ULL|ll|LL|ul|UL|u|U|l|L)\b", r"\1", expression)
    previous = None
    while previous != expression:
        previous = expression
        expression = re.sub(
            r"(?:static_cast|base::strict_cast)\s*<[^<>]+>\s*\(([^()]*)\)",
            r"(\1)", expression,
        )
    names = set(re.findall(r"\b(?:V8[A-Za-z0-9_]+::Constant::)?k[A-Za-z0-9_]+\b", expression))
    for name in sorted(names, key=len, reverse=True):
        qualified = name if "::" in name else name
        if qualified in resolving:
            return None
        short = qualified.rsplit("::", 1)[-1]
        replacement = None
        for key, candidate in definitions.items():
            if key == qualified or key.rsplit("::", 1)[-1] == short:
                replacement = _constant_expression_value(candidate, definitions, resolving + (key,))
                if replacement is not None:
                    break
        if replacement is None:
            continue
        expression = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", str(replacement), expression)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return None

    def evaluate(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert)):
            operand = evaluate(node.operand)
            return +operand if isinstance(node.op, ast.UAdd) else -operand if isinstance(node.op, ast.USub) else ~operand
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.BitOr, ast.BitAnd, ast.BitXor, ast.LShift, ast.RShift, ast.FloorDiv, ast.Div)):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.BitOr): return left | right
            if isinstance(node.op, ast.BitAnd): return left & right
            if isinstance(node.op, ast.BitXor): return left ^ right
            if isinstance(node.op, ast.LShift): return left << right
            if isinstance(node.op, ast.RShift): return left >> right
            if isinstance(node.op, ast.FloorDiv): return left // right
            return left / right
        return None

    try:
        result = evaluate(tree.body)
    except (ArithmeticError, TypeError, ValueError):
        return None
    return result if isinstance(result, (int, float)) and not isinstance(result, bool) else None


def _constant_definitions(root):
    """Collect generated ``V8Foo::Constant::kValue`` definitions from headers."""
    definitions = {}
    for path in sorted(Path(root).rglob("*.h")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        classes = re.findall(r"\bclass(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+(V8[A-Za-z0-9_]+)\b", text)
        if not classes:
            continue
        owner = classes[0]
        for match in re.finditer(
            r"\bstatic\s+constexpr\s+[A-Za-z0-9_:<>]+\s+(k[A-Za-z0-9_]+)\s*=\s*([^;]+);",
            text,
        ):
            definitions[f"{owner}::Constant::{match.group(1)}"] = match.group(2).strip()
    return definitions


#: The callback tuple both property handler configurations use, in order.
HANDLER_ROLES = ("get", "set", "query", "del", "enum", "definer")


def _handler_configurations(text, skip=()):
    """``{named/indexed: {role: callback}}`` from the configurations in *text*.

    Spans in *skip* are cross-origin configurations, whose callbacks belong to the
    cross-origin family rather than the interface's own handlers.
    """
    found = {}
    labels = (("named", "NamedPropertyHandlerConfiguration"),
              ("indexed", "IndexedPropertyHandlerConfiguration"))
    for kind, label in labels:
        for match in re.finditer(label + r"\s*\(([^;]*?)\)", text, re.S):
            if any(start <= match.start() < end for start, end in skip):
                continue
            data = {}
            for role, callback in zip(HANDLER_ROLES, split_top_level(match.group(1))):
                callback = _clean_callback_reference(callback)
                if callback and callback != "nullptr":
                    data[role] = callback
            if data:
                found[kind] = data
    return found


def _cross_origin_spans(text):
    """The spans of every ``SetAccessCheckCallbackAndHandler`` call."""
    spans = []
    for match in re.finditer(r"SetAccessCheckCallbackAndHandler\s*\(", text):
        try:
            spans.append((match.start(), balanced_end(text, match.end() - 1, "(", ")")))
        except ValueError:
            continue
    return spans


def _cross_origin_handler_names(text):
    """Cross-origin callbacks named in generated helper functions instead."""
    found = {"named": {}, "indexed": {}}
    for kind in ("named", "indexed"):
        for role in HANDLER_ROLES:
            patterns = (
                rf"CrossOrigin{kind.title()}[^\n]*{role}",
                rf"{kind.title()}Property[^\n]*CrossOrigin[^\n]*{role}",
            )
            for pattern in patterns:
                match = re.search(pattern, text, re.I)
                if match:
                    callback = re.search(r"([A-Za-z_][A-Za-z0-9_]*Callback)", match.group(0))
                    if callback:
                        found[kind][role] = callback.group(1)
                        break
    return found


def extract_template(text):
    """Extract V8 template switches and interceptor callback names.

    Chromium changes formatting frequently, so this records conservative evidence
    rather than parsing C++ expressions.
    """
    #: The definition kind the generated WrapperTypeInfo states is what decides the interface
    #: object: a namespace is a ``v8::ObjectTemplate`` (``kIdlNamespace``,
    #: ``WrapperTypeInfo::GetV8ClassTemplate``) and a callback interface has no prototype at all
    #: (``kWrapperTypeNoPrototype``, ``SetupIDLCallbackInterfaceTemplate``).  The kind is read
    #: first because a namespace has no prototype either: the shape on its own called all seven
    #: namespaces (``CSS`` and the six ``GPU*``) callback interfaces.
    namespace = bool(re.search(r"\bkIdlNamespace\b", text))
    no_prototype = bool(re.search(r"\bkWrapperTypeNoPrototype\b", text))
    template = {
        "immutable_proto": bool(re.search(r"SetImmutableProto\s*\(", text)),
        "undetectable": bool(re.search(r"MarkAsUndetectable\s*\(", text)),
        "global_this": bool(re.search(r"SetAccessCheckCallback[^;]*(?:global_this|GlobalThis)", text, re.I)),
        #: One field, three numbers: the kind decides the interface object, and reading it first
        #: is what keeps ``CSS`` from reading as a callback interface -- it has no prototype
        #: either.  An ordinary interface states nothing, so the fragment of a split binding that
        #: carries no ``WrapperTypeInfo`` (``modules/v8/v8_css.cc`` beside ``core/v8/v8_css.cc``)
        #: cannot erase the kind the other one declares.
        "definition_kind": DefinitionKind.kIdlNamespace if namespace else (
            DefinitionKind.kWrapperTypeNoPrototype if no_prototype else None),
        "unconstructable": bool(re.search(r"(?:Set(?:Call|Construct)Handler|Unconstructable)[^;]*kDontAll", text)),
        #: The generated tables install the iterator; the four named methods come from the IDL
        #: (``iterable<>``/``maplike<>``/``setlike<>``) or from members the tables declare.
        "array": ArrayProto.iterator if re.search(r"SetIntrinsicDataProperty[^;]*Array|ArrayPrototype", text) else None,
        "call_handler": None,
        "access_check": None,
    }
    call = re.search(r"SetCallAsFunctionHandler\s*\(\s*([A-Za-z_][A-Za-z0-9_:]*)", text)
    if call:
        template["call_handler"] = call.group(1)
    access = re.search(r"SetAccessCheckCallback(?:AndHandler)?\s*\(\s*([A-Za-z_][A-Za-z0-9_:]*)", text)
    if access:
        template["access_check"] = {
            "callback": access.group(1),
            "with_handler": bool(re.search(r"SetAccessCheckCallbackAndHandler\s*\(", text)),
        }
    cross_origin = _cross_origin_spans(text)
    handlers = {"named": {}, "indexed": {}, "cross_origin": {"named": {}, "indexed": {}}}
    handlers.update(_handler_configurations(text, skip=cross_origin))
    for span in cross_origin:
        for kind, data in _handler_configurations(text[span[0]:span[1]]).items():
            handlers["cross_origin"][kind].update(data)
    if not any(handlers["cross_origin"].values()):
        # Older bindings name cross-origin callbacks in helper functions, not inline.
        handlers["cross_origin"] = _cross_origin_handler_names(text)
    template["handlers"] = handlers
    return template


def extract_constants(members, definitions=None):
    """Normalize constant table entries with literal/expression dual form."""
    constants = []
    for location, values in members.items():
        for member in values:
            if member.get("kind") != "constant":
                continue
            expression = str(member.get("value", ""))
            value = _constant_expression_value(expression, definitions or {})
            item = {"name": member.get("name"), "expression": expression,
                    "resolved": value is not None,
                    "value": value, "location": location}
            constants.append(item)
    return constants


def _merge_members(target, incoming):
    for location, entries in incoming.items():
        known = {(item.get("kind"), item.get("name"), item.get("location")) for item in target[location]}
        for entry in entries:
            key = (entry.get("kind"), entry.get("name"), entry.get("location"))
            if key not in known: target[location].append(entry); known.add(key)
            elif entry.get("kind") == "operation":
                existing = next(item for item in target[location] if (item.get("kind"), item.get("name"), item.get("location")) == key)
                if existing.get("callback") != entry.get("callback"): existing.setdefault("overloads", []).append(entry)


def _merge_template(current, incoming):
    """Merge template evidence from split generated binding fragments."""
    result = dict(current or {})
    for key, value in (incoming or {}).items():
        if key == "array":
            #: Fragments may disagree; the fuller shape wins -- a fact about the interface.
            strength = {ArrayProto.iterable: 2, ArrayProto.iterator: 1}
            if strength.get(value, 0) > strength.get(result.get(key), 0):
                result[key] = value
            continue
        if key == "handlers":
            handlers = result.setdefault("handlers", {})
            for group, entries in (value or {}).items():
                if isinstance(entries, dict):
                    handlers.setdefault(group, {}).update(entries)
                elif entries:
                    handlers[group] = entries
        elif isinstance(value, bool):
            result[key] = bool(result.get(key)) or value
        elif value is not None:
            result[key] = value
    return result


def _canonical_declaration(text, match):
    end = balanced_end(text, text.find("{", match.start())); body = text[text.find("{", match.start()) + 1:end - 1]
    tag = (re.search(r'"([A-Za-z0-9_]+)"', body) or [None, match.group("binding")])[1]
    parent_match = re.search(r"V8([A-Za-z0-9_]+)::GetWrapperTypeInfo\(\)", body)
    has_constructor = bool(CONSTRUCTOR_CALLBACK.search(text))
    # Blink constructors use V8's throwing constructor; legacy factory functions do not make it callable.
    allow_call = False if has_constructor else None
    return tag, parent_match.group(1) if parent_match else None, has_constructor, allow_call


def _exposure(text):
    result = []
    if re.search(r"IsWindow\(\)", text): result.append("Window")
    if re.search(r"Is(?:Dedicated|Shared)?WorkerGlobalScope\(\)", text): result.append("Worker")
    return result


#: The IDL declarations that publish the iterator **and** the four named methods.  ``async
#: iterable<T>`` publishes ``@@asyncIterator`` alone, which no word here states.
_IDL_ITERABLE = re.compile(r"\b(?:iterable|maplike|setlike)\s*<")


def _idl_array_shape(body):
    """``ArrayProto.iterable`` when the IDL publishes the four named methods, else ``None``.

    This is the reading the generated tables cannot give: ``NodeList``'s binding installs the
    iterator intrinsic but never lists ``entries``/``keys``/``values``/``forEach``, so without
    the IDL it reads as the iterator alone.  Measured against Chrome on 2026-10-07, 12 of the
    908 interfaces a window exposes were misread that way.
    """
    for match in _IDL_ITERABLE.finditer(body):
        before = body[:match.start()].rstrip().split()[-1:]
        if before == ["async"]:
            continue
        return ArrayProto.iterable
    return None


def _idl_metadata(root):
    """Read Blink IDL extended attributes needed by V8 template setup."""
    metadata = {}
    for path in sorted(root.rglob("*.idl")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in IDL_INTERFACE_DECL.finditer(text):
            if match.group("partial"):
                # A partial declaration adds members to an interface another file owns.
                # Its body must not decide the interface's exposure, constructor or
                # constants: reading it as the interface's own replaced URL's
                # ``[Exposed=(Window,Worker,ShadowRealm)] constructor(...)`` with a
                # partial's silence, which is why ``new URL()`` was refused.
                continue
            try:
                end = balanced_end(text, text.find("{", match.start()))
            except ValueError:
                continue
            attrs, body = match.group("attrs"), text[text.find("{", match.start()) + 1:end - 1]
            attrs = attrs or ""
            exposure_match = re.search(
                r"\bExposed\s*=\s*(?:\(([^)]*)\)|(\*)|([A-Za-z_][A-Za-z0-9_]*))", attrs)
            exposed_in = []
            if exposure_match:
                if exposure_match.group(2):
                    # ``Exposed=*`` is every global; this runtime has two realm kinds.
                    exposed_in = ["Window", "Worker"]
                else:
                    exposed_in = re.findall(r"[A-Za-z_][A-Za-z0-9_]*",
                                            exposure_match.group(1) or exposure_match.group(3) or "")
            constants = {}
            for constant in re.finditer(r"\bconst\s+[A-Za-z_][A-Za-z0-9_<>? ]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+)", body):
                expression = constant.group(2).strip()
                constants[constant.group(1)] = {"expression": expression,
                                                "value": _primitive_constant(expression),
                                                "resolved": _primitive_constant(expression) is not None}
            metadata[match.group("name")] = {
                "parent": match.group("parent"),
                "properties": _idl_property_families(body),
                "exposed_in": exposed_in,
                "exposed": (bool(exposed_in) and "LegacyNoInterfaceObject" not in attrs)
                if exposure_match or "LegacyNoInterfaceObject" in attrs else None,
                "allow_new": bool(re.search(r"\bconstructor\s*\(", body)),
                "allow_call": bool(re.search(r"\bLegacyCallAsFunction\b", attrs)),
                "constants": constants,
                "aliases": re.findall(r"\bLegacyWindowAlias\s*=\s*([A-Za-z_][A-Za-z0-9_]*)", attrs),
                "array": _idl_array_shape(body),
            }
    return metadata


def _idl_property_families(body):
    """``{named/indexed: {get, set}}`` for the property getters an IDL body declares."""
    found = {"named": {}, "indexed": {}}
    for match in re.finditer(r"(?m)^\s*(?:\[[^\]]*\]\s*)?(getter|setter)\s+([^;]+);", body):
        # Named getters take a DOMString, indexed getters take an index.
        kind = "indexed" if re.search(r"\b(?:unsigned\s+)?(?:long|short)\b", match.group(2)) else "named"
        found[kind]["set" if match.group(1) == "setter" else "get"] = True
    return found


def _idl_handler_findings(extracted, metadata):
    """Generated handlers the interface's own IDL does not declare.

    A named family needs a named getter or setter; indexed families are exempt,
    because Blink also generates indexed callbacks for named-only interfaces.
    """
    findings = []
    for name, item in extracted.items():
        info = metadata.get(name)
        if name.startswith("_") or info is None:
            continue
        named = ((item.get("template") or {}).get("handlers") or {}).get("named") or {}
        if named and not info["properties"]["named"]:
            findings.append({
                "interface": name,
                "family": "named",
                "handlers": sorted(named),
                "reason": "no named getter or setter in the interface's IDL",
            })
    return findings


def _apply_idl_metadata(extracted, metadata):
    for name, item in extracted.items():
        if name.startswith("_") or name not in metadata:
            continue
        info = metadata[name]
        if info["parent"]:
            item["parent"] = info["parent"]
        if info["exposed_in"]:
            item["exposed_in"] = info["exposed_in"]
        if info.get("aliases"):
            item["aliases"] = sorted(set(item.get("aliases", ())) | set(info["aliases"]))
        if info["exposed"] is not None:
            item["constructor"]["exposed"] = info["exposed"]
        # Generated constructor callbacks are authoritative; IDL metadata only fills gaps.
        item["constructor"]["allow_new"] = bool(item["constructor"].get("allow_new") or info["allow_new"])
        item["constructor"]["allow_call"] = bool(item["constructor"].get("allow_call") or info["allow_call"])
        if info.get("constants"):
            known = {entry.get("name"): entry for entry in item.get("constants", ())}
            for constant_name, constant in info["constants"].items():
                known[constant_name] = {"name": constant_name, "location": "interface", **constant}
            item["constants"] = list(known.values())
            for member in item.get("members", {}).get("interface", ()):
                if member.get("kind") == "constant" and member.get("name") in info["constants"]:
                    member.update(info["constants"][member["name"]])


def _publishes_the_four(item):
    """Whether the generated tables declare the four named iterator methods as operations."""
    names = {member.get("name")
             for member in (item.get("members") or {}).get("prototype") or []
             if member.get("kind") == "operation"}
    return {"entries", "keys", "values", "forEach"} <= names


def _apply_array_shape(extracted, metadata):
    """State what each interface borrows from ``Array.prototype``, as the bits ``array`` carries.

    Three readings, strongest first: the interface's IDL (``iterable<>``, ``maplike<>`` or
    ``setlike<>`` publishes the four named methods as well as the iterator), the four operations
    the generated tables declare, and the intrinsic those tables install (the iterator alone).
    A run without ``--idl-root`` still has the last two, and an interface that borrows nothing
    carries no key at all rather than a false one.
    """
    for name, item in extracted.items():
        if name.startswith("_") or not isinstance(item, dict):
            continue
        template = item.setdefault("template", {})
        info = metadata.get(name) or {}
        if info.get("array") == ArrayProto.iterable or _publishes_the_four(item):
            template["array"] = ArrayProto.iterable
        elif not template.get("array"):
            template.pop("array", None)


def _filename_interface(path, text):
    """Infer the canonical interface for module-only binding fragments."""
    match = re.search(r"V8([A-Za-z0-9_]+)::(?:Impl::|Install)", text)
    if match:
        binding = match.group(1)
        # Generated aliases such as DOMURL are represented by V8URL.
        if binding.startswith("DOM") and len(binding) > 3:
            binding = binding[3:]
        return binding
    stem = path.stem
    if stem.startswith("v8_"):
        return "".join(part[:1].upper() + part[1:] for part in stem[3:].split("_"))
    return None


def extract(root, idl_root=None):
    root = Path(root)
    constant_definitions = _constant_definitions(root)
    result, parse_failures, duplicate_sources = {}, [], 0
    for path in sorted(root.rglob("v8_*.cc")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        declarations = list(INTERFACE_DECL.finditer(text))
        if not declarations:
            inferred = _filename_interface(path, text)
            if inferred and inferred in result:
                item = result[inferred]
                relative = str(path).replace("/", "\\")
                if relative not in item["sources"]:
                    item["sources"].append(relative)
                    duplicate_sources += 1
                _merge_members(item["members"], extract_members(text))
                item["exposed_in"] = sorted(set(item.get("exposed_in", ())) | set(_exposure(text)))
                item["template"] = _merge_template(item.get("template") or {}, extract_template(text))
            continue
        for declaration in declarations:
            try:
                name, parent, constructible, allow_call = _canonical_declaration(text, declaration)
                members = extract_members(text)
                callback_signatures = extract_callback_signatures(text)
            except (ValueError, TypeError): parse_failures.append(str(path)); continue
            relative = str(path).replace("/", "\\"); item = result.get(name)
            if item is None:
                exposure = _exposure(text)
                constructor_callback = next((callback for callback in callback_signatures if callback.endswith("ConstructorCallback")), None)
                item = result[name] = {"parent": parent, "sources": [relative],
                    "constructor": {"exposed": bool(exposure), "allow_new": constructible,
                    "allow_call": allow_call, "length": next((sig.get("required", 0) for callback, sig in callback_signatures.items() if callback.endswith("ConstructorCallback")), None),
                    "signature": next((sig for callback, sig in callback_signatures.items() if callback.endswith("ConstructorCallback")), {"status": "unresolved", "parameters": [], "source_evidence": "constructor callback not found"}),
                    "callback": constructor_callback},
                    "tag": name, "members": {"instance": [], "prototype": [], "interface": []}, "exposed_in": _exposure(text)}
                item["template"] = extract_template(text)
            else:
                duplicate_sources += 1
                if relative not in item["sources"]: item["sources"].append(relative)
                if parent and not item.get("parent"): item["parent"] = parent
                item["constructor"]["exposed"] |= bool(_exposure(text)); item["constructor"]["allow_new"] |= constructible; item["constructor"]["allow_call"] |= allow_call
                item["exposed_in"] = sorted(set(item.get("exposed_in", ())) | set(_exposure(text)))
                item["template"] = _merge_template(item.get("template") or {}, extract_template(text))
            _merge_members(item["members"], members)
    for item in result.values():
        if not isinstance(item, dict):
            continue
        item["sources"] = sorted(set(item["sources"]))
        if not item["exposed_in"]: item.pop("exposed_in", None)
        extracted_constants = {entry.get("name"): entry for entry in extract_constants(item.get("members") or {}, constant_definitions)}
        for entry in item.get("constants", ()):
            name = entry.get("name")
            current = extracted_constants.get(name)
            if current is None or (not current.get("resolved") and entry.get("resolved")):
                extracted_constants[name] = entry
        item["constants"] = list(extracted_constants.values())
    metadata = {}
    findings = []
    if idl_root is not None:
        metadata = _idl_metadata(Path(idl_root))
        _apply_idl_metadata(result, metadata)
        findings = _idl_handler_findings(result, metadata)
    #: The IDL raises the reading to ``"iterable"`` where the tables never list the four.
    _apply_array_shape(result, metadata)
    result["_report"] = _report(result, duplicate_sources, parse_failures, findings)
    return result


def _signature_status(signature):
    return (signature or {}).get("status", "unresolved")


def _report(result, duplicate_sources=0, parse_failures=(), handler_findings=()):
    interfaces = {name: item for name, item in result.items() if not name.startswith("_")}
    signatures = []
    for item in interfaces.values():
        signatures.append((item.get("constructor") or {}).get("signature") or {})
        for values in item["members"].values():
            for member in values:
                if member.get("kind") == "operation":
                    signatures.append(member.get("signature") or {})
                elif member.get("kind") == "attribute" and member.get("setter"):
                    signatures.append(member.get("setter_signature") or {})
    return {
        "interfaces": len(interfaces),
        "member_total": sum(sum(len(values) for values in item["members"].values()) for item in interfaces.values()),
        "interfaces_with_members": sum(any(item["members"].values()) for item in interfaces.values()),
        "interfaces_with_signatures": sum(any(member.get("signature") for values in item["members"].values() for member in values) for item in interfaces.values()),
        "generated_signatures": sum(_signature_status(signature) == "generated" for signature in signatures),
        "unresolved_signatures": sum(_signature_status(signature) == "unresolved" for signature in signatures),
        "duplicate_sources": duplicate_sources,
        "parse_failures": sorted(set(parse_failures)),
        "handler_findings": list(handler_findings),
    }


def _compact_callback_metadata(item):
    """Remove source callback names while preserving runtime behavior flags."""
    (item.get("constructor") or {}).pop("callback", None)
    (item.get("constructor") or {}).pop("signature", None)
    template = item.get("template") or {}
    if template.get("call_handler") is not None:
        template["call_handler"] = 1
    access_check = template.get("access_check")
    if isinstance(access_check, dict) and access_check.get("callback") is not None:
        access_check.pop("callback", None)
        access_check["enabled"] = 1
    handlers = template.get("handlers") or {}
    for group in ("named", "indexed"):
        for role in tuple((handlers.get(group) or {}).keys()):
            handlers[group][role] = 1
        cross = handlers.get("cross_origin") or {}
        for role in tuple((cross.get(group) or {}).keys()):
            cross[group][role] = 1


def _compact_member_callback_metadata(member):
    """Keep what the runtime reads: signatures, callback symbols, overloads and the reviewer's
    raw fields are not read by a binding, and one world bit stands in for the world's name."""
    member.pop("getter", None)
    member.pop("callback", None)
    member.pop("signature", None)
    member.pop("setter_signature", None)
    member.pop("overloads", None)
    member.pop("review", None)
    member.pop("raw", None)
    member["main_world"] = member.pop("world", None) in (None, "AllWorlds", "MainWorld")
    if isinstance(member.get("property_flags"), int):
        member.pop("flags", None)


def idl_interfaces(extracted):
    """The interfaces a binding reads, and nothing a reviewer would.

    ``array`` is stated as the bits the interface borrows (``pyv8.ArrayProto.iterator`` or
    ``pyv8.ArrayProto.iterable``, absent when it borrows nothing).
    """
    output = {}
    for name, source in extracted.items():
        if name.startswith("_"):
            continue
        item = json.loads(json.dumps(source))
        item.pop("provenance", None)
        item.pop("sources", None)
        item.setdefault("parent", None)
        item.setdefault("tag", name)
        item.setdefault("exposed_in", [])
        constructor = item.setdefault("constructor", {})
        constructor.setdefault("signature", {"status": "unresolved", "parameters": []})
        template = item.setdefault("template", extract_template(""))
        template.setdefault("handlers", {"named": {}, "indexed": {}, "cross_origin": {"named": {}, "indexed": {}}})
        item.setdefault("constants", extract_constants(item.get("members") or {}))
        for constant in item["constants"]:
            constant.setdefault("location", "interface")
            constant.pop("expression", None)
        for location, members in item["members"].items():
            runtime_members = []
            for member in members:
                if member.get("kind") == "constant":
                    continue
                member.pop("location", None)
                if member.get("kind") == "attribute":
                    member.pop("readonly", None)
                    member["setter"] = 1 if member.get("setter") else None
                _compact_member_callback_metadata(member)
                runtime_members.append(member)
            item["members"][location] = runtime_members
        _compact_callback_metadata(item)
        output[name] = item
    return output


def generated_bindings_dir(build_dir: Path) -> Path:
    """Return Chromium's generated Blink V8 binding directory."""
    candidates = (
        build_dir / "gen" / "third_party" / "blink" / "renderer" / "bindings",
        build_dir / "gen" / "third_party" / "blink" / "renderer",
    )
    for candidate in candidates:
        if any(candidate.rglob("v8_*.cc")):
            return candidate
    return candidates[0]


#: The artifact a release ships: what a bare ``python tools/extract_idl.py`` regenerates.
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "idl" / "idl.zip"


def main():
    parser = argparse.ArgumentParser(description="Parse Chromium-generated Blink V8 bindings into an interface baseline.")
    parser.add_argument("output", type=Path, nargs="?", default=DEFAULT_OUTPUT,
                        help="output path (default: %(default)s); a .zip path is compressed")
    parser.add_argument("--chromium-root", type=Path, default=DEFAULT_CHROMIUM_ROOT,
                        help="Chromium checkout root (default: %(default)s)")
    parser.add_argument("--build-dir", type=Path, default=None,
                        help="GN output directory (default: <chromium-root>/src/out/Cronet145)")
    parser.add_argument("--bindings-dir", type=Path, default=None,
                        help="existing generated bindings directory")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--chromium-version", default=None, metavar="X.Y.Z.W",
                        help="release the bindings came from"
                             " (default: read from <chromium-root>/src/chrome/VERSION)")
    args = parser.parse_args()
    if args.chromium_version is not None and not VERSION_PATTERN.fullmatch(args.chromium_version):
        parser.error(f"invalid Chromium version {args.chromium_version!r}; expected e.g. 154.0.8030.0")
    build_dir = args.build_dir or (args.chromium_root / "src" / "out" / "Cronet145")
    output_path = args.output
    renderer = args.bindings_dir or generated_bindings_dir(build_dir)
    if not any(renderer.rglob("v8_*.cc")):
        parser.error(f"generated Blink bindings not found under {renderer}; run fetch_chromium.py first")
    try:
        chromium_version = args.chromium_version or read_chromium_version(args.chromium_root)
    except ValueError as error:
        parser.error(str(error))
    extracted = extract(renderer, args.chromium_root / "src" / "third_party" / "blink" / "renderer")
    report = extracted.pop("_report")
    document = {
        "idl": {"version": IDL_VERSION, "source": "tools/extract_idl.py",
                "chromium_version": chromium_version, "report": report},
        "interfaces": idl_interfaces(extracted),
    }
    write_idl(output_path, document)
    if args.report: print(json.dumps(report, indent=2, sort_keys=True))

if __name__ == "__main__": main()
