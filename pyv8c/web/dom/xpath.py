"""XPath, for the expressions this DOM can answer honestly.

The subset: absolute/relative paths, the child/descendant/self/parent/attribute
axes, ``*``/``@name``/``text()``/``node()``/``comment()`` and positional,
attribute and child-name predicates.  An expression outside it is rejected when
evaluated rather than answered with the wrong nodes.
"""

import re

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, dom_string, integer, missing_error
from ..page_errors import PageTypeError, dom_exception

_AXES = ("child", "descendant", "self", "parent", "attribute")
_PREDICATE_ATTRIBUTE = re.compile(r"^@([\w.-]+)\s*=\s*(['\"])(.*?)\2$")
_PREDICATE_HAS_ATTRIBUTE = re.compile(r"^@([\w.-]+)$")
_PREDICATE_CHILD = re.compile(r"^[\w.-]+$")


def _split_steps(text):
    """The expression's steps, with ``//`` marked as the descendant separator."""
    steps, depth, current = [], 0, ""
    for character in text:
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth < 0:
                return None
        if character == "/" and depth == 0:
            steps.append(current)
            current = ""
        else:
            current += character
    if depth:
        return None
    steps.append(current)
    return steps


def _parse_test(body):
    """A step's node test, or None when this DOM does not know the form."""
    if body == "*":
        return "element", None
    if body == ".":
        return "self", None
    if body == "..":
        return "parent", None
    if body.startswith("@"):
        return "attribute", body[1:]
    if body in ("text()", "comment()", "node()"):
        return body[:-2], None
    if re.match(r"^[A-Za-z_][\w.-]*$", body):
        return "element", body
    return None


def _parse_predicate(text):
    """A predicate as a callable over the candidates so far, or None."""
    text = text.strip()
    if text.isdigit():
        position = int(text)
        return lambda nodes: nodes[position - 1:position] if position else []
    match = _PREDICATE_ATTRIBUTE.match(text)
    if match:
        name, value = match.group(1), match.group(3)
        return lambda nodes: [node for node in nodes
                              if str(node.attributes.get(name, "")) == value]
    match = _PREDICATE_HAS_ATTRIBUTE.match(text)
    if match:
        name = match.group(1)
        return lambda nodes: [node for node in nodes if name in node.attributes]
    if _PREDICATE_CHILD.match(text):
        return lambda nodes: [node for node in nodes
                              if any(getattr(child, "localName", "") == text
                                     for child in node._children)]
    return None


def compile_expression(expression):
    """A plan for *expression*, or None when it is valid but out of this subset.

    A syntax error (empty expression, unbalanced brackets, unknown axis) is raised
    here, which is where ``createExpression`` reports it.
    """
    text = str(expression).strip()
    if not text:
        raise dom_exception("SyntaxError",
                            "Failed to execute 'createExpression': The expression is empty.")
    steps = _split_steps(text)
    if steps is None:
        raise dom_exception("SyntaxError",
                            "Failed to execute 'createExpression': Unbalanced brackets.")
    plan, descendant = [], False
    for index, step in enumerate(steps):
        step = step.strip()
        if not step:
            # A separator: the leading one is the root the walk starts at; a later bare
            # ``//`` descends from there.
            if index:
                descendant = True
            continue
        axis, body = "child", step
        if "::" in body:
            axis, _, body = body.partition("::")
            if axis not in _AXES:
                return None
        predicates = re.findall(r"\[([^\]]*)\]", body)
        body = re.sub(r"\[[^\]]*\]", "", body).strip()
        test = _parse_test(body)
        if test is None:
            return None
        parsed = [_parse_predicate(predicate) for predicate in predicates]
        if any(predicate is None for predicate in parsed):
            return None
        if descendant:
            axis, descendant = "descendant", False
        plan.append((axis, test, tuple(parsed)))
    return plan


def _candidates(node, axis):
    """The nodes one axis reaches from *node*."""
    if axis == "self":
        return [node]
    if axis == "parent":
        parent = getattr(node, "parentNode", None)
        return [parent] if parent is not None else []
    if axis == "attribute":
        return [node]
    if axis == "descendant":
        return list(node._descendants())
    return list(getattr(node, "_children", ()))


def _matches(node, test, axis):
    """Whether *node* is what the step's node test asks for."""
    kind, name = test
    if kind == "attribute":
        return False
    if kind == "node":
        return True
    if kind == "text":
        return getattr(node, "nodeType", None) == 3
    if kind == "comment":
        return getattr(node, "nodeType", None) == 8
    if kind == "self":
        return True
    if kind == "parent":
        return True
    if name is None:
        return getattr(node, "nodeType", None) == 1
    return getattr(node, "localName", None) == name


def evaluate_plan(plan, context):
    """The nodes *plan* selects from *context*, in document order.  Each step runs
    per context node, so ``//p[2]`` is the second ``p`` of each parent, as specified."""
    nodes = [context]
    for axis, test, predicates in plan:
        selected = []
        for node in nodes:
            candidates = _candidates(node, axis)
            if test[0] == "attribute":
                matches = [candidate.fn_getAttributeNode(test[1])
                           for candidate in candidates
                           if str(test[1]) in getattr(candidate, "attributes", {})]
            else:
                matches = [candidate for candidate in candidates
                           if _matches(candidate, test, axis)]
            for predicate in predicates:
                matches = predicate(matches)
            selected.extend(matches)
        nodes = selected
    return nodes


@interface('XPathResult', None)
class XPathResult:
    ANY_TYPE = 0
    NUMBER_TYPE = 1
    STRING_TYPE = 2
    BOOLEAN_TYPE = 3
    UNORDERED_NODE_ITERATOR_TYPE = 4
    ORDERED_NODE_ITERATOR_TYPE = 5
    UNORDERED_NODE_SNAPSHOT_TYPE = 6
    ORDERED_NODE_SNAPSHOT_TYPE = 7
    ANY_UNORDERED_NODE_TYPE = 8
    FIRST_ORDERED_NODE_TYPE = 9

    def __init__(self, nodes=(), requested=0, **_):
        self._nodes = list(nodes)
        self._position = 0
        # ANY_TYPE gives an unordered iterator, the node-set result a page normally walks.
        if requested in (self.ANY_TYPE,):
            requested = self.UNORDERED_NODE_ITERATOR_TYPE
        self._type = requested

    def get_resultType(self): return self._type
    def get_invalidIteratorState(self): return False

    def _string_value(self):
        """The XPath string-value of the first node in document order."""
        if not self._nodes:
            return ""
        node = self._nodes[0]
        if getattr(node, "nodeType", None) == 1:
            return str(node.get_textContent() or "")
        return str(getattr(node, "nodeValue", "") or "")

    def get_stringValue(self):
        return self._string_value() if self._type == self.STRING_TYPE else self._wrong("stringValue")

    def get_numberValue(self):
        if self._type != self.NUMBER_TYPE:
            return self._wrong("numberValue")
        try:
            return float(self._string_value().strip())
        except ValueError:
            return float("nan")

    def get_booleanValue(self):
        if self._type != self.BOOLEAN_TYPE:
            return self._wrong("booleanValue")
        return bool(self._nodes)

    def get_singleNodeValue(self):
        if self._type == self.ANY_UNORDERED_NODE_TYPE:
            return self._nodes[0] if self._nodes else pyv8.Null
        if self._type == self.FIRST_ORDERED_NODE_TYPE:
            return self._nodes[0] if self._nodes else pyv8.Null
        return self._wrong("singleNodeValue")

    def get_snapshotLength(self):
        if self._type in (self.UNORDERED_NODE_SNAPSHOT_TYPE, self.ORDERED_NODE_SNAPSHOT_TYPE):
            return len(self._nodes)
        return self._wrong("snapshotLength")

    def fn_snapshotItem(self, index=0, *_extra):
        if self._type not in (self.UNORDERED_NODE_SNAPSHOT_TYPE, self.ORDERED_NODE_SNAPSHOT_TYPE):
            return self._wrong("snapshotItem")
        position = int(integer(index, signed=True))
        return self._nodes[position] if 0 <= position < len(self._nodes) else pyv8.Null

    def fn_iterateNext(self, *_extra):
        if self._type not in (self.UNORDERED_NODE_ITERATOR_TYPE, self.ORDERED_NODE_ITERATOR_TYPE):
            return self._wrong("iterateNext")
        if self._position >= len(self._nodes):
            return pyv8.Null
        node = self._nodes[self._position]
        self._position += 1
        return node

    def _wrong(self, member):
        raise PageTypeError(
            f"Failed to read the '{member}' property from 'XPathResult':"
            f" The result type is {self._result_type_name()}.")


@interface('XPathExpression', None)
class XPathExpression:
    def __init__(self, expression="", plan=None, **_):
        self._expression, self._plan = str(expression), plan

    def fn_evaluate(self, context=MISSING, type=0, result=None, *_extra):
        if context is MISSING:
            raise missing_error('XPathExpression', 'evaluate', 2, 0, constructor=False)
        if self._plan is None:
            raise dom_exception(
                "NotSupportedError",
                f"Failed to execute 'evaluate' on 'XPathExpression': \"{self._expression}\" is"
                " outside the XPath subset this DOM evaluates.")
        nodes = evaluate_plan(self._plan, context)
        return XPathResult(nodes, type)


@interface('XPathEvaluator', None)
class XPathEvaluator:
    def __init__(self, *_extra, **_options):
        """An evaluator holds nothing: it is the document's XPath entry point."""

    def fn_createExpression(self, expression=MISSING, resolver=None, *_extra):
        return create_expression(expression)

    def fn_createNSResolver(self, node=MISSING, *_extra):
        """The node itself resolves names here, as the specification allows."""
        if node is MISSING:
            raise missing_error('XPathEvaluator', 'createNSResolver', 1, 0, constructor=False)
        return node

    def fn_evaluate(self, expression=MISSING, context=MISSING, resolver=None, type=0,
                    result=None, *_extra):
        if expression is MISSING:
            raise missing_error('XPathEvaluator', 'evaluate', 2, 0, constructor=False)
        if context is MISSING:
            raise missing_error('XPathEvaluator', 'evaluate', 2, 1, constructor=False)
        return create_expression(expression).fn_evaluate(context, type, result)


def create_expression(expression=MISSING):
    """An ``XPathExpression`` for *expression*."""
    if expression is MISSING:
        raise missing_error('XPathEvaluator', 'createExpression', 1, 0, constructor=False)
    text = dom_string(expression)
    return XPathExpression(text, compile_expression(text))

__all__ = ["XPathEvaluator", "XPathExpression", "XPathResult", "create_expression"]
