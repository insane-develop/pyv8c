"""The two DOM traversals: ``NodeIterator`` walks a flat run, ``TreeWalker`` moves.

Both share the same collection rule, which is what the filter's three answers mean: a
node the filter *accepts* is handed out, a node it *skips* is stepped over but its
children are still visited, and a node it *rejects* takes its whole subtree with it.
"""
import pyv8
from ...idl import interface
from ...foundation.arguments import (MISSING, callback_value, integer, is_nullish,
                                     missing_error)
from ..page_errors import PageTypeError


#: What a filter answers, and what ``whatToShow`` masks are built from.
FILTER_ACCEPT, FILTER_REJECT, FILTER_SKIP = 1, 2, 3
SHOW_ELEMENT, SHOW_ATTRIBUTE, SHOW_TEXT, SHOW_CDATA_SECTION = 0x1, 0x2, 0x4, 0x8
SHOW_ENTITY_REFERENCE, SHOW_ENTITY, SHOW_PROCESSING_INSTRUCTION = 0x10, 0x20, 0x40
SHOW_COMMENT, SHOW_DOCUMENT, SHOW_DOCUMENT_TYPE = 0x80, 0x100, 0x200
SHOW_DOCUMENT_FRAGMENT, SHOW_NOTATION, SHOW_ALL = 0x400, 0x800, 0xFFFFFFFF


def _js_call(fn, this, *args):
    if isinstance(fn, pyv8.JSObject):
        return fn.call(this, *args)
    return fn(*args)


def _filter_of(value):
    """The filter a traversal was handed; ``None`` when there is none."""
    if value is MISSING or is_nullish(value):
        return None
    return callback_value(value)


def _mask(node_type):
    return 1 << (int(node_type) - 1) if int(node_type) > 0 else 0


class _Traversal:
    """What both traversals share: the root, the mask and the filter."""

    def __init__(self, root=None, what_to_show=None, filter=None, services=None, **_):
        self._root = root
        self._what_to_show = (SHOW_ALL if is_nullish(what_to_show)
                              else integer(what_to_show, bits=32))
        self._filter = _filter_of(filter)
        self._services = services

    def _accept(self, node):
        """``FILTER_ACCEPT``, ``FILTER_REJECT`` or ``FILTER_SKIP`` for one node."""
        if not (_mask(getattr(node, "nodeType", 0)) & int(self._what_to_show)):
            return FILTER_SKIP
        if self._filter is None:
            return FILTER_ACCEPT
        answer = integer(_js_call(self._filter, pyv8.Null, node), signed=True, bits=16)
        if answer not in (FILTER_ACCEPT, FILTER_REJECT, FILTER_SKIP):
            raise PageTypeError("The filter function returned a value that is not one of"
                              " the NodeFilter constants.")
        return answer

    def _children(self, node):
        return getattr(node, "_children", None) or ()

    def _sibling(self, node, step):
        """The sibling *step* places away, or ``None`` at the ends."""
        parent = getattr(node, "parentNode", None)
        if parent is None:
            return None
        siblings = list(self._children(parent))
        try:
            position = siblings.index(node)
        except ValueError:
            return None
        position += step
        return siblings[position] if 0 <= position < len(siblings) else None

    def _next_in_collection(self, node):
        """The node the traversal sees next, with a rejected subtree left out."""
        if self._accept(node) != FILTER_REJECT and self._children(node):
            return self._children(node)[0]
        current = node
        while current is not None and current is not self._root:
            sibling = self._sibling(current, 1)
            if sibling is not None:
                return sibling
            current = getattr(current, "parentNode", None)
        return None

    def _last_descendant(self, node):
        """The last node inside *node*, skipping the subtrees a filter rejects."""
        current = node
        while True:
            children = list(self._children(current))
            while children and self._accept(children[-1]) == FILTER_REJECT:
                children.pop()
            if not children:
                return current
            current = children[-1]

    def _previous_in_collection(self, node):
        """The node the traversal sees before *node*, its rejected siblings left out."""
        candidate = self._sibling(node, -1)
        while candidate is not None:
            if self._accept(candidate) != FILTER_REJECT:
                return self._last_descendant(candidate)
            candidate = self._sibling(candidate, -1)
        parent = getattr(node, "parentNode", None)
        if parent is None or node is self._root:
            return None
        return parent


@interface('NodeFilter', None)
class NodeFilter:
    """Only its constants matter; a page never builds one."""

    FILTER_ACCEPT, FILTER_REJECT, FILTER_SKIP = FILTER_ACCEPT, FILTER_REJECT, FILTER_SKIP
    SHOW_ALL, SHOW_ELEMENT, SHOW_ATTRIBUTE, SHOW_TEXT = SHOW_ALL, SHOW_ELEMENT, SHOW_ATTRIBUTE, SHOW_TEXT
    SHOW_CDATA_SECTION, SHOW_ENTITY_REFERENCE, SHOW_ENTITY = (SHOW_CDATA_SECTION,
                                                             SHOW_ENTITY_REFERENCE, SHOW_ENTITY)
    SHOW_PROCESSING_INSTRUCTION, SHOW_COMMENT = SHOW_PROCESSING_INSTRUCTION, SHOW_COMMENT
    SHOW_DOCUMENT, SHOW_DOCUMENT_TYPE, SHOW_DOCUMENT_FRAGMENT = (SHOW_DOCUMENT,
                                                                SHOW_DOCUMENT_TYPE,
                                                                SHOW_DOCUMENT_FRAGMENT)
    SHOW_NOTATION = SHOW_NOTATION


@interface('NodeIterator', None)
class NodeIterator(_Traversal):
    """A flat run over the collection, with a pointer that sits before or after a node."""

    def __init__(self, root=None, what_to_show=None, filter=None, **kw):
        super().__init__(root, what_to_show, filter, **kw)
        self._reference = root
        self._before = True

    def get_root(self): return self._root
    def get_referenceNode(self): return self._reference
    def get_pointerBeforeReferenceNode(self): return self._before
    def get_whatToShow(self): return self._what_to_show
    def get_filter(self): return pyv8.Null if self._filter is None else self._filter

    def fn_nextNode(self, *_extra):
        node, before = self._reference, self._before
        while True:
            if not before:
                node = self._next_in_collection(node)
                if node is None:
                    return pyv8.Null
            before = False
            if self._accept(node) == FILTER_ACCEPT:
                self._reference, self._before = node, before
                return node

    def fn_previousNode(self, *_extra):
        node, before = self._reference, self._before
        while True:
            if before:
                node = self._previous_in_collection(node)
                if node is None:
                    return pyv8.Null
            before = True
            if self._accept(node) == FILTER_ACCEPT:
                self._reference, self._before = node, before
                return node

    def fn_detach(self, *_extra):
        """Nothing to release: the specification keeps it for compatibility only."""


@interface('TreeWalker', None)
class TreeWalker(_Traversal):
    """The same collection, with a current node a page can move and assign."""

    def __init__(self, root=None, what_to_show=None, filter=None, **kw):
        super().__init__(root, what_to_show, filter, **kw)
        self._current = root

    def get_root(self): return self._root
    def get_whatToShow(self): return self._what_to_show
    def get_filter(self): return pyv8.Null if self._filter is None else self._filter
    def get_currentNode(self): return self._current
    def set_currentNode(self, value): self._current = value

    def _take(self, node):
        self._current = node
        return node

    def fn_parentNode(self, *_extra):
        node = self._current
        while node is not None and node is not self._root:
            node = getattr(node, "parentNode", None)
            if node is None:
                return pyv8.Null
            if self._accept(node) == FILTER_ACCEPT:
                return self._take(node)
        return pyv8.Null

    def _is_inside(self, node, start):
        current = node
        while current is not None:
            if current is start:
                return True
            current = getattr(current, "parentNode", None)
        return False

    def _accepted_inside(self, start, last):
        """The first or last node the filter accepts below *start*, rejected out."""
        found = None
        node = self._next_in_collection(start)
        while node is not None and self._is_inside(node, start):
            if self._accept(node) == FILTER_ACCEPT:
                if not last:
                    return node
                found = node
            node = self._next_in_collection(node)
        return found

    def fn_firstChild(self, *_extra):
        """What Chrome hands back is the first accepted node below the current one."""
        node = self._accepted_inside(self._current, False)
        return pyv8.Null if node is None else self._take(node)

    def fn_lastChild(self, *_extra):
        node = self._accepted_inside(self._current, True)
        return pyv8.Null if node is None else self._take(node)

    def fn_nextSibling(self, *_extra):
        return self._sibling_step(1)

    def fn_previousSibling(self, *_extra):
        return self._sibling_step(-1)

    def _sibling_step(self, step):
        node = self._current
        if node is self._root:
            return pyv8.Null
        while node is not None:
            sibling = self._sibling(node, step)
            while sibling is not None:
                node = sibling
                if self._accept(node) == FILTER_ACCEPT:
                    return self._take(node)
                sibling = self._sibling(node, step)
            node = getattr(node, "parentNode", None)
            if node is None or node is self._root:
                return pyv8.Null
        return pyv8.Null

    def fn_nextNode(self, *_extra):
        node = self._current
        result = FILTER_ACCEPT
        while True:
            if result != FILTER_REJECT:
                while self._children(node):
                    node = self._children(node)[0]
                    result = self._accept(node)
                    if result == FILTER_ACCEPT:
                        return self._take(node)
            following = None
            while following is None:
                if node is self._root:
                    return pyv8.Null
                following = self._sibling(node, 1)
                if following is None:
                    node = getattr(node, "parentNode", None)
                    if node is None:
                        return pyv8.Null
            node = following
            result = self._accept(node)
            if result == FILTER_ACCEPT:
                return self._take(node)

    def fn_previousNode(self, *_extra):
        node = self._current
        while node is not self._root:
            sibling = self._sibling(node, -1)
            while sibling is not None:
                node = sibling
                result = self._accept(node)
                while result != FILTER_REJECT and self._children(node):
                    node = self._children(node)[-1]
                    result = self._accept(node)
                if result == FILTER_ACCEPT:
                    return self._take(node)
                sibling = self._sibling(node, -1)
            node = getattr(node, "parentNode", None)
            if node is None:
                return pyv8.Null
            if self._accept(node) == FILTER_ACCEPT:
                return self._take(node)
        return pyv8.Null


def create_node_iterator(document, root, what_to_show, filter):
    return NodeIterator(root=root, what_to_show=what_to_show, filter=filter,
                        services=getattr(document, "_services", None))


def create_tree_walker(document, root, what_to_show, filter):
    return TreeWalker(root=root, what_to_show=what_to_show, filter=filter,
                      services=getattr(document, "_services", None))

__all__ = ["NodeFilter", "NodeIterator", "TreeWalker",
           "create_node_iterator", "create_tree_walker"]
