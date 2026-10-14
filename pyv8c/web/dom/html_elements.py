"""HTML element implementations: the tag-specific interfaces and the tag-to-class
factory."""
import re
from html import escape
from urllib.parse import urljoin, urlsplit, urlunsplit

import pyv8
from pyv8 import AttributeSpec

from ...idl import interface
from ...foundation.arguments import MISSING, boolean, dictionary, dom_string, integer, interface_value, is_nullish, missing_error
from ..page_errors import InvalidCharacterError, NotFoundError, NotSupportedError, PageSyntaxError, PageTypeError, dom_exception
from ...foundation.urls import credentials_netloc
from ...foundation.urls import origin_string
from .character_data import Text
from .collections import (
    DOMStringMap,
    DOMTokenList,
    HTMLCollection,
    HYPERLINK_REL_TOKENS,
    LINK_REL_TOKENS,
    NamedNodeMap,
    tag_name_ns_collection,
)
from . import aria, html_props
from .css import CSSStyleDeclaration
from .css_typed import StylePropertyMap
from .events import _install_handler_slots
from .mixins import _ChildNodeMixin, _NonDocumentTypeChildNodeMixin, _ParentNodeMixin
from .mutation import record_mutation
from .nodes import Attr, DocumentFragment, Node, ShadowRoot
from .utils import _VALID_NAME_RE, XHTML_NAMESPACE


#: ``insertAdjacent*`` positions, in the order Chromium validates them.
_ADJACENT_POSITIONS = ("beforebegin", "afterbegin", "beforeend", "afterend")


# HTML void elements serialize without children or an end tag, programmatic nodes too.
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


@interface('Element', 'Node')
class Element(_ParentNodeMixin, _ChildNodeMixin, _NonDocumentTypeChildNodeMixin, Node):
    """Base implementation for HTML and XML elements."""

    # Chrome reads ``role`` back as the attribute or null, and never validates it.
    def get_role(self):
        value = self.attributes.get("role")
        return pyv8.Null if value is None else str(value)
    def set_role(self, value): self.attributes["role"] = str(dom_string(value))

    #: What Chrome will put a shadow tree on: these tags, and any autonomous custom
    #: element, which is a name with a hyphen in it.
    _SHADOW_HOSTS = frozenset((
        "article", "aside", "blockquote", "body", "div", "footer", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "main", "nav", "p", "section", "span"))

    def get_shadowRoot(self):
        """The tree again, or null: a closed one is the host's own business."""
        root = getattr(self, "_shadow_root", None)
        if root is None or root.get_mode() != "open":
            return pyv8.Null
        return root
    def fn_attachShadow(self, init=MISSING, *_extra):
        """The one tree a host gets. A closed root is still handed back here; it is
        only ``host.shadowRoot`` that stops reporting it.
        """
        options = init if isinstance(init, dict) else (
            dictionary(init) if isinstance(init, pyv8.JSObject) else None)
        if options is None:
            raise PageTypeError("Failed to execute 'attachShadow' on 'Element': The provided"
                              " value is not of type 'ShadowRootInit'.")
        mode = options.get("mode", pyv8.Undefined)
        if is_nullish(mode):
            raise PageTypeError("Failed to execute 'attachShadow' on 'Element': Failed to"
                              " read the 'mode' property from 'ShadowRootInit': Required"
                              " member is undefined.")
        mode = str(dom_string(mode))
        if mode not in ("open", "closed"):
            raise PageTypeError("Failed to execute 'attachShadow' on 'Element': Failed to"
                              " read the 'mode' property from 'ShadowRootInit': The"
                              f" provided value '{mode}' is not a valid enum value of type"
                              " ShadowRootMode.")
        if getattr(self, "_shadow_root", None) is not None:
            raise NotSupportedError("Failed to execute 'attachShadow' on 'Element': Shadow"
                                    " root cannot be created on a host which already hosts"
                                    " a shadow tree.")
        name = str(self.localName).lower()
        if name not in self._SHADOW_HOSTS and "-" not in name:
            raise NotSupportedError("Failed to execute 'attachShadow' on 'Element': This"
                                    " element does not support attachShadow")
        root = self._shadow_root = ShadowRoot(mode, self, services=self._services)
        return root

    def __init__(self, tag_name="div", services=None, attributes=None, namespace=None,
                 prefix=None, local_name=None, **kw):
        """Build one element with the name it was created under.

        *namespace* is ``None`` on the HTML path -- ``createElement`` and the parser,
        which lower-case the name they were given -- and the namespace URI on the XML
        path, where the name is kept as written and only an HTML-namespace element
        reports it in upper case.
        """
        qualified = str(tag_name)
        if namespace is None:
            local = (qualified if local_name is None else str(local_name)).lower()
            node_name = local.upper()
            self._namespace, self._prefix = XHTML_NAMESPACE, None
        else:
            local = qualified if local_name is None else str(local_name)
            node_name = qualified.upper() if namespace == XHTML_NAMESPACE else qualified
            self._namespace = None if is_nullish(namespace) else str(namespace)
            self._prefix = None if is_nullish(prefix) else str(prefix)
        super().__init__(services=services, node_type=Node.ELEMENT_NODE, node_name=node_name, **kw)
        self.localName, self.attributes = local, dict(attributes or {})

    def get_namespaceURI(self): return self._namespace or pyv8.Null
    def get_prefix(self): return self._prefix or pyv8.Null
    def get_localName(self): return self.localName
    def get_tagName(self): return self.nodeName
    def get_id(self): return self.attributes.get("id", "")
    def set_id(self, value):
        value = dom_string(value)
        self.attributes['id'] = str(value)
    def get_className(self): return self.attributes.get("class", "")
    def set_className(self, value):
        value = dom_string(value)
        self.attributes['class'] = str(value)
    def get_innerHTML(self): return "".join(c.outer_html() for c in self._children)
    def set_innerHTML(self, value):
        value = dom_string(value)
        self._replace_children(())
        if self._services: self._services.document_services.document._parse_fragment(str(value), self)
    def get_outerHTML(self): return self.outer_html()
    def set_outerHTML(self, value):
        value = dom_string(value)
        if self.parentNode: self.parentNode.fn_replaceChild(self._services.document_services.document._parse_one(str(value)), self)
    def get_children(self): return HTMLCollection(owner=self, getter=lambda: [c for c in self._children if isinstance(c, Element)])
    def get_slot(self): return str(self.attributes.get("slot", ""))
    def set_slot(self, value): self.attributes["slot"] = str(dom_string(value))
    def get_part(self):
        value = getattr(self, "_part_list", None)
        if value is None: value = self._part_list = DOMTokenList(self, "part")
        return value
    def set_part(self, value):
        # PutForwards, so an assignment lands on the token list's own value.
        self.get_part().set_value(dom_string(value))
    def get_assignedSlot(self): return pyv8.Null
    # Nothing here scrolls and nothing lays a border out, so the four read as a page
    # reads them on an element that is not rendered: Chrome answers 0 for each.
    def get_scrollTop(self): return 0
    def set_scrollTop(self, value): return None
    def get_scrollLeft(self): return 0
    def set_scrollLeft(self, value): return None
    def get_clientTop(self): return 0
    def get_clientLeft(self): return 0
    def get_elementTiming(self): return str(self.attributes.get("elementtiming", ""))
    def set_elementTiming(self, value):
        self.attributes["elementtiming"] = str(dom_string(value))
    def get_currentCSSZoom(self): return 1
    def get_customElementRegistry(self):
        """The registry ``window.customElements`` hands out, since it is one per window."""
        from ..bom.window import Window
        document = self.ownerDocument
        view = document.get_defaultView() if document is not None else None
        if not isinstance(view, Window):
            return pyv8.Null
        return view._window_value("customElements")
    def get_activeViewTransition(self): return pyv8.Null

    def get_classList(self):
        value = getattr(self, "_class_list", None)
        if value is None: value = self._class_list = DOMTokenList(self)
        return value
    def get_attributes(self):
        value = getattr(self, "_attributes_view", None)
        if value is None: value = self._attributes_view = NamedNodeMap(self)
        return value
    def get_clientWidth(self):
        from .layout import layout
        return int(round(layout(self).padding_box_width))
    def get_clientHeight(self):
        from .layout import layout
        return int(round(layout(self).padding_box_height))
    def get_scrollWidth(self): return self.get_clientWidth()
    def get_scrollHeight(self): return self.get_clientHeight()
    def fn_getBoundingClientRect(self, *_extra):
        from .layout import positioned_box
        from .rect import DOMRect
        box = positioned_box(self)
        return DOMRect(box.left, box.top, box.width, box.height)
    def fn_getAttribute(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'getAttribute', 1, 0, constructor=False)
        name = dom_string(name)
        return self.attributes.get(str(name).lower(), pyv8.Null)
    def fn_setAttribute(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'setAttribute', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('Element', 'setAttribute', 2, 1, constructor=False)
        self._set_attribute(dom_string(name), dom_string(value))
    def fn_removeAttribute(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'removeAttribute', 1, 0, constructor=False)
        name = dom_string(name)
        lowered = str(name).lower()
        previous = self.attributes.pop(lowered, None)
        if previous is not None:
            record_mutation(self, "attributes", attribute_name=lowered, old_value=previous)
            self._attribute_changed_here(lowered, previous, None)
    def fn_hasAttribute(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'hasAttribute', 1, 0, constructor=False)
        name = dom_string(name)
        return str(name).lower() in self.attributes
    def fn_hasAttributes(self, *_extra):
        return bool(self.attributes)
    def fn_getAttributeNames(self, *_extra):
        return list(self.attributes)
    def fn_toggleAttribute(self, name=MISSING, force=pyv8.Undefined, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'toggleAttribute', 1, 0, constructor=False)
        name = dom_string(name)
        if force is not pyv8.Undefined:
            force = boolean(force)
        name = str(name).lower()
        if force in (None, pyv8.Undefined, pyv8.Null):
            force = name not in self.attributes
        force = bool(force)
        if force: self.attributes[name] = ""
        else: self.attributes.pop(name, None)
        return force
    # Only the HTML namespace exists, so the *NS variants behave like their plain
    # counterparts for a null/HTML namespace and match nothing for a foreign one.
    def fn_getAttributeNS(self, namespace=MISSING, local_name=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Element', 'getAttributeNS', 2, 0, constructor=False)
        if local_name is MISSING:
            raise missing_error('Element', 'getAttributeNS', 2, 1, constructor=False)
        if not is_nullish(namespace):
            namespace = dom_string(namespace)
        local_name = dom_string(local_name)
        return self.fn_getAttribute(local_name)
    def fn_setAttributeNS(self, namespace=MISSING, name=MISSING, value=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Element', 'setAttributeNS', 3, 0)
        if name is MISSING:
            raise missing_error('Element', 'setAttributeNS', 3, 1)
        if value is MISSING:
            raise missing_error('Element', 'setAttributeNS', 3, 2)
        if not is_nullish(namespace):
            # Only the HTML namespace exists, but WebIDL converts the argument anyway.
            dom_string(namespace)
        self._set_attribute(dom_string(name), dom_string(value))

    def _set_attribute(self, name, value):
        """Write one attribute; both spellings convert their arguments first.

        The notification lives here rather than in the two spellings, because the
        native dispatch resolves a member on the class that declared it: a subclass
        override of ``fn_setAttribute`` is never reached.
        """
        if not _VALID_NAME_RE.match(name):
            raise InvalidCharacterError("The attribute name provided is invalid.")
        lowered = name.lower()
        previous = self.attributes.get(lowered)
        written = str(value)
        self.attributes[lowered] = written
        record_mutation(self, "attributes", attribute_name=lowered,
                        old_value=previous)
        self._attribute_changed_here(lowered, previous, written)

    def _attribute_changed_here(self, name, old, new):
        """Tell an element that reflects the attribute, when it wants to know."""
        changed = getattr(self, "_attribute_changed", None)
        if changed is not None and old != new:
            changed(name, old, new)
    def fn_removeAttributeNS(self, namespace=MISSING, local_name=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Element', 'removeAttributeNS', 2, 0, constructor=False)
        if local_name is MISSING:
            raise missing_error('Element', 'removeAttributeNS', 2, 1, constructor=False)
        if not is_nullish(namespace):
            namespace = dom_string(namespace)
        local_name = dom_string(local_name)
        self.fn_removeAttribute(local_name)
    def fn_hasAttributeNS(self, namespace=MISSING, local_name=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Element', 'hasAttributeNS', 2, 0, constructor=False)
        if local_name is MISSING:
            raise missing_error('Element', 'hasAttributeNS', 2, 1, constructor=False)
        if not is_nullish(namespace):
            namespace = dom_string(namespace)
        local_name = dom_string(local_name)
        return self.fn_hasAttribute(local_name)
    def fn_getAttributeNode(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'getAttributeNode', 1, 0, constructor=False)
        name = dom_string(name)
        name = str(name).lower()
        if name not in self.attributes: return pyv8.Null
        return Attr(name, self.attributes[name], ownerElement=self, services=self._services)
    def fn_setAttributeNode(self, attr=MISSING, *_extra):
        if attr is MISSING:
            raise missing_error('Element', 'setAttributeNode', 1, 0, constructor=False)
        attr = interface_value(attr, 'Attr', 'Element', 'setAttributeNode', 1, nullable=False)
        if not isinstance(attr, Attr): raise PageTypeError("parameter 1 is not of type 'Attr'.")
        previous = self.fn_getAttributeNode(attr.get_name())
        self.attributes[str(attr.get_name()).lower()] = attr.get_value()
        return previous
    def fn_removeAttributeNode(self, attr=MISSING, *_extra):
        if attr is MISSING:
            raise missing_error('Element', 'removeAttributeNode', 1, 0, constructor=False)
        attr = interface_value(attr, 'Attr', 'Element', 'removeAttributeNode', 1, nullable=False)
        if not isinstance(attr, Attr): raise PageTypeError("parameter 1 is not of type 'Attr'.")
        name = str(attr.get_name()).lower()
        if name not in self.attributes: raise NotFoundError("The attribute was not found.")
        return Attr(name, self.attributes.pop(name), ownerElement=self, services=self._services)
    def _descendant_elements(self):
        return [node for node in self._descendant_snapshot() if isinstance(node, Element)]
    def fn_getElementsByTagName(self, tag_name=MISSING, *_extra):
        if tag_name is MISSING:
            raise missing_error('Element', 'getElementsByTagName', 1, 0, constructor=False)
        tag_name = dom_string(tag_name)
        wanted = str(tag_name).lower()
        return HTMLCollection(owner=self, getter=lambda: [
            node for node in self._descendant_elements()
            if wanted == "*" or node.localName == wanted
        ])
    def fn_getElementsByClassName(self, class_names=MISSING, *_extra):
        if class_names is MISSING:
            raise missing_error('Element', 'getElementsByClassName', 1, 0, constructor=False)
        class_names = dom_string(class_names)
        wanted = set(str(class_names).split())
        return HTMLCollection(owner=self, getter=lambda: [
            node for node in self._descendant_elements()
            if wanted.issubset(set(node.get_className().split()))
        ])
    def fn_getElementsByTagNameNS(self, namespace=MISSING, local_name=MISSING, *_extra):
        return tag_name_ns_collection(self, 'Element', namespace, local_name)
    def _insert_adjacent(self, where, nodes):
        where = str(where).lower()
        if where not in _ADJACENT_POSITIONS:
            raise PageSyntaxError(
                f"The value provided ('{where}') is not one of "
                + ", ".join(f"'{p}'" for p in _ADJACENT_POSITIONS) + "."
            )
        if where in ("afterbegin", "beforeend"):
            for offset, node in enumerate(nodes):
                self._adopt_child(node, offset if where == "afterbegin" else None)
            return
        parent = self.parentNode
        if parent is None:
            return
        for node in nodes:
            # Re-locate ``self`` each step: adopting a sibling detaches it and shifts the list.
            index = parent._children.index(self) + (0 if where == "beforebegin" else 1)
            parent._adopt_child(node, index)
    def fn_insertAdjacentElement(self, where=MISSING, element=MISSING, *_extra):
        if where is MISSING:
            raise missing_error('Element', 'insertAdjacentElement', 2, 0, constructor=False)
        if element is MISSING:
            raise missing_error('Element', 'insertAdjacentElement', 2, 1, constructor=False)
        where = dom_string(where)
        element = interface_value(element, 'Element', 'Element', 'insertAdjacentElement', 2, nullable=False)
        if not isinstance(element, Element):
            raise PageTypeError("parameter 2 is not of type 'Element'")
        self._insert_adjacent(where, [element])
        return element
    def fn_insertAdjacentText(self, where=MISSING, data=MISSING, *_extra):
        if where is MISSING:
            raise missing_error('Element', 'insertAdjacentText', 2, 0, constructor=False)
        if data is MISSING:
            raise missing_error('Element', 'insertAdjacentText', 2, 1, constructor=False)
        where = dom_string(where)
        data = dom_string(data)
        self._insert_adjacent(where, [Text(str(data), services=self._services)])
    def fn_insertAdjacentHTML(self, position=MISSING, html=MISSING, *_extra):
        if position is MISSING:
            raise missing_error('Element', 'insertAdjacentHTML', 2, 0, constructor=False)
        if html is MISSING:
            raise missing_error('Element', 'insertAdjacentHTML', 2, 1, constructor=False)
        position = dom_string(position)
        html = dom_string(html)
        document = self._services.document_services.document
        fragment = DocumentFragment(services=self._services)
        document._parse_fragment(str(html), fragment)
        self._insert_adjacent(position, list(fragment._children))
    def fn_webkitMatchesSelector(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'webkitMatchesSelector', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self.fn_matches(selector)
    def fn_querySelector(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'querySelector', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self._services.document_services.document.query_selector(selector, self) if self._services else pyv8.Null
    def fn_querySelectorAll(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'querySelectorAll', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self._services.document_services.document.query_selector_all(selector, self) if self._services else []
    def fn_matches(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'matches', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self._services.document_services.document.matches(self, selector) if self._services else False
    def fn_closest(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'closest', 1, 0, constructor=False)
        selector = dom_string(selector)
        n = self
        while isinstance(n, Element):
            if n.fn_matches(selector): return n
            n = n.parentElement
        return pyv8.Null
    @property
    def _reflected(self):
        """What a member was last given and then had taken away, per attribute.

        Chrome answers such a member with the word it was given rather than with the
        attribute or the default, which is the only way popover reads back as null.
        """
        store = self.__dict__.get("_reflected_store")
        if store is None:
            store = self.__dict__["_reflected_store"] = {}
        return store

    def _write(self, name, value, keep=None):
        """Set or remove one content attribute.

        A nullish value takes the attribute away unless *keep* says which of the two
        ends it stands for, which is how a boolean attribute spells itself: the true
        end is an empty attribute, the false end is no attribute.
        """
        if keep is not None:
            if keep:
                self.attributes[name] = ""
            else:
                self.attributes.pop(name, None)
        elif is_nullish(value):
            self.attributes.pop(name, None)
        else:
            self.attributes[name] = str(dom_string(value))

    def _replace_with_text(self, value):
        """Take this element out of its parent and leave a text node in its place."""
        parent = self.parentNode
        if parent is None:
            return
        if is_nullish(value):
            parent.removeChild(self)
            return
        parent.fn_replaceChild(Text(str(dom_string(value)), services=self._services), self)

    def _resolve_ids(self, ids):
        """The elements *ids* names, in the order given, skipping what is not there.

        The idref ARIA members answer with elements rather than with the string, and
        an id that names nothing contributes nothing -- which is why a tree outside a
        document resolves to an empty answer.
        """
        document = self.ownerDocument
        if document is None or not self.isConnected:
            return []
        by_id = {}
        for node in document._descendants():
            if isinstance(node, Element) and node.get_id():
                by_id.setdefault(node.get_id(), node)
        found = []
        for identifier in ids:
            target = by_id.get(identifier)
            if target is not None:
                found.append(target)
        return found

    def outer_html(self):
        attrs = "".join(f' {k}="{escape(str(v), quote=True)}"' for k, v in self.attributes.items())
        if self.localName in _VOID_ELEMENTS:
            return f"<{self.localName}{attrs}>"
        return f"<{self.localName}{attrs}>{self.get_innerHTML()}</{self.localName}>"
    def _clone_shallow(self):
        options = dict(services=self._services, attributes=self.attributes.copy(),
                       namespace=self._namespace, prefix=self._prefix, local_name=self.localName)
        # Baseline-only SVG classes have a placeholder constructor. As in
        # Document.createElementNS, initialize their inherited Element model directly.
        if getattr(type(self), '__v8_idl_stub__', False):
            clone = type(self).__new__(type(self))
            Element.__init__(clone, self.nodeName, **options)
            return clone
        return type(self)(self.nodeName, **options)

    # A click runs the behavior of the nearest ancestor that has one (see
    # dom.activation); most elements have none, hence the default pair below.

    def _activatable(self):
        """Whether a click at or below this element runs a behavior here."""
        return False

    def _run_activation(self, event=None):
        """What a click does to this element beyond dispatching the event."""
        return None


#: The clipboard handlers belong to Element, not to HTMLElement, and the ARIA members
#: do too, so both are installed on Element rather than in the table every target
#: shares. The ARIA table is data measured from Chrome, which is why it lives in its
#: own module.
#: Accessors only: the baseline says which of these Element publishes.
_install_handler_slots(Element, ("onbeforecopy", "onbeforecut", "onbeforepaste",
                                 "onfullscreenchange", "onfullscreenerror",
                                 "onwebkitfullscreenchange", "onwebkitfullscreenerror"))
aria.install(Element)


def _unrendered(node):
    """Whether a descendant is laid out away, taking its subtree out of innerText.

    Reading ``innerText`` on such an element itself still reports its text, the way
    Chrome falls back to the text content for something it does not render.
    """
    from .layout import computed_style

    if not isinstance(node, Element):
        return False
    name = str(getattr(node, "localName", "") or "").lower()
    if name in ("script", "style", "link", "meta", "head", "template", "title", "noscript"):
        return True
    if node.attributes.get("hidden") is not None:
        return True
    return str(computed_style(node).get("display", "")) == "none"


@interface('HTMLElement', 'Element')
class HTMLElement(Element):
    #: The attributes this interface reflects in a shape of its own, from a table measured on
    #: Chrome; see web.dom.html_props for what each shape does.  Reading them changes nothing.
    __idl_overrides__ = {name: {"side_effect": 1} for name in (
        "accessKey", "autocapitalize", "autocorrect", "autofocus", "dir", "draggable",
        "editContext", "enterKeyHint", "focusGroup", "focusGroupStart", "inert", "inputMode",
        "lang", "outerText", "popover", "spellcheck", "translate", "virtualKeyboardPolicy",
        "writingSuggestions",
    )}

    #: The tags whose boundaries innerText turns into newlines. Chrome decides this
    #: from the rendered display, which this tree does not have, so the block-level
    #: HTML elements stand in for it.
    _BLOCK_LEVEL = frozenset((
        "address", "article", "aside", "blockquote", "details", "dialog", "dd", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "main", "nav", "ol",
        "p", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr",
        "ul"))

    #: What the contenteditable attribute reads back as. An empty attribute and
    #: "true" both mean true, and anything the IDL enum does not hold -- an unknown
    #: keyword or no attribute at all -- reads as "inherit".
    _EDITABLE_VALUES = {"": "true", "true": "true", "false": "false",
                        "plaintext-only": "plaintext-only", "inherit": "inherit"}

    def get_contentEditable(self):
        value = self.attributes.get("contenteditable")
        if value is None:
            return "inherit"
        return self._EDITABLE_VALUES.get(str(value).lower(), "inherit")

    def set_contentEditable(self, value):
        """Set the keyword, or refuse what the IDL enum does not hold.

        Chrome throws a SyntaxError for anything but the four keywords, writes the
        keyword in lower case, and takes the attribute away for "inherit".
        """
        text = str(dom_string(value))
        keyword = text.lower()
        if keyword not in ("true", "false", "plaintext-only", "inherit"):
            raise dom_exception(
                "SyntaxError",
                "Failed to set the 'contentEditable' property on 'HTMLElement': The value"
                f" provided ('{text}') is not one of 'true', 'false', 'plaintext-only', or"
                " 'inherit'.")
        if keyword == "inherit":
            self.attributes.pop("contenteditable", None)
        else:
            self.attributes["contenteditable"] = keyword

    def get_isContentEditable(self):
        """Whether this element is editable: the document's design mode, or the
        nearest contenteditable at or above it. A detached tree is included, as in
        Chrome, while design mode reaches only what is in a document.
        """
        document = self.ownerDocument
        if self.isConnected and getattr(document, "design_mode", "off") == "on":
            return True
        node = self
        while node is not None:
            if isinstance(node, HTMLElement):
                value = node.attributes.get("contenteditable")
                if value is not None:
                    keyword = self._EDITABLE_VALUES.get(str(value).lower(), "inherit")
                    if keyword != "inherit":
                        return keyword != "false"
            node = node.parentNode
        return False

    def get_innerText(self):
        """The text as it would be shown: whitespace collapsed, a block boundary and a
        ``br`` written as newlines, and the ends trimmed -- except in ``pre`` and
        ``textarea``, where the spaces at either end are content rather than layout.
        """
        text = self._rendered_text(self, self.isConnected)
        if str(getattr(self, "localName", "") or "").lower() in ("pre", "textarea"):
            return text
        return text.strip()

    def _rendered_text(self, node, laid_out):
        """The text of *node* and its descendants as it would be shown.

        Chrome decides the line breaks from what it rendered, so a block boundary
        only becomes a newline once the element is in a document: a tree nobody
        attached reads as one run of text, which is what a detached element's
        innerText does in Chrome too. Whitespace collapses unless the element keeps
        it, as ``pre`` and ``textarea`` do. What the page lays out away -- a script
        or style element, a hidden one, anything ``display: none`` reaches -- takes
        its whole subtree out of the result.
        """
        if isinstance(node, Text):
            return node.get_data()
        name = str(getattr(node, "localName", "") or "").lower()
        if name == "br":
            return "\n" if laid_out else ""
        joined = "".join(self._rendered_text(child, laid_out)
                         for child in getattr(node, "_children", ())
                         if not (laid_out and _unrendered(child)))
        if laid_out and name in self._BLOCK_LEVEL:
            joined = "\n" + joined + "\n"
        if name in ("pre", "textarea"):
            return joined
        collapsed = re.sub(r"[ \t\r\f\v]+", " ", joined)
        return re.sub(r" *\n *", "\n", collapsed)

    def set_innerText(self, value):
        """Replace the children with the text, one line break at a time.

        The text is taken as written -- markup in it is not parsed -- and a newline
        becomes a ``br`` rather than a character in the text, which is what makes the
        setter and the getter round-trip. Null clears the element.
        """
        from .html_classes import class_for_tag

        self._replace_children(())
        if is_nullish(value):
            return
        for index, line in enumerate(re.split(r"\r\n|\r|\n", str(dom_string(value)))):
            if index:
                self.append_child(class_for_tag("br")("br", services=self._services))
            if line:
                self.append_child(Text(line, services=self._services))

    def get_nonce(self): return self._attr("nonce")
    def set_nonce(self, value): self._set_string("nonce", value)
    def get_title(self): return self.attributes.get("title", "")
    def set_title(self, v):
        v = dom_string(v)
        self.attributes['title'] = str(v)
    def get_hidden(self): return "hidden" in self.attributes
    def set_hidden(self, v):
        v = boolean(v)
        if v: self.attributes["hidden"] = ""
        else: self.attributes.pop("hidden", None)
    def _attr(self, name):
        """The value of a content attribute as a string, ``""`` when absent."""
        value = self.attributes.get(name)
        return "" if value is None else str(value)
    def _set_string(self, name, value):
        self.attributes[name] = str(dom_string(value))
    def _bool(self, name): return name in self.attributes
    def _set_bool(self, name, value):
        if boolean(value): self.attributes[name] = ""
        else: self.attributes.pop(name, None)
    def get_style(self):
        value = getattr(self, "_style_declaration", None)
        if value is None:
            value = self._style_declaration = CSSStyleDeclaration(element=self)
        return value
    def get_attributeStyleMap(self):
        value = getattr(self, "_attribute_style_map", None)
        if value is None:
            value = self._attribute_style_map = StylePropertyMap(element=self)
        return value
    def get_dataset(self):
        value = getattr(self, "_dataset_map", None)
        if value is None:
            value = self._dataset_map = DOMStringMap(self)
        return value
    def get_offsetParent(self):
        from .layout import offset_parent
        return offset_parent(self) or pyv8.Null
    def _offset_box(self):
        """This element's box, and where its ``offsetParent`` measures from."""
        from .layout import layout, offset_parent
        parent = offset_parent(self)
        box = layout(self)
        if parent is None:
            return box, 0.0, 0.0
        parent_box = layout(parent)
        return box, parent_box.padding_box_left, parent_box.padding_box_top
    def get_offsetTop(self):
        box, _left, top = self._offset_box()
        return int(round(box.top - top))
    def get_offsetLeft(self):
        box, left, _top = self._offset_box()
        return int(round(box.left - left))
    def get_offsetWidth(self):
        from .layout import layout
        return int(round(layout(self).width))
    def get_offsetHeight(self):
        from .layout import layout
        return int(round(layout(self).height))

    def get_tabIndex(self):
        """The element's tab index: the attribute when present, else Chromium's default
        -- 0 for a focusable area, -1 otherwise; an unparseable value reads as 0."""
        from .focus import focusable
        value = self.attributes.get("tabindex")
        if value is None:
            return 0 if focusable(self) else -1
        digits = str(value).strip().lstrip("+-")
        return int(digits) if digits.isdigit() else 0

    def set_tabIndex(self, value):
        self.attributes["tabindex"] = str(integer(value, signed=True))

    def fn_focus(self, *_extra):
        """Focus this element when it is a focusable area; Chrome ignores the call on
        anything else, so the check lives in :func:`dom.focus.focus`."""
        from .focus import focus
        focus(self)

    def fn_blur(self, *_extra):
        """Drop focus, when this element is the one holding it."""
        from .focus import blur
        document = self.ownerDocument
        if document is not None and getattr(document, "_focused_element", None) is self:
            blur(document)

    def fn_click(self, *_extra):
        """Run the synthetic click activation steps a script asked for: an untrusted
        ``click`` at the origin, then the behavior unless a listener cancelled it."""
        from .activation import click_element
        click_element(self)





#: The accessors for the reflected attributes above; ``html_props`` gives the class one
#: getter/setter pair per member, and the convention binds them to the members it states.
html_props.install(HTMLElement)


@interface('HTMLUnknownElement', 'HTMLElement')
class HTMLUnknownElement(HTMLElement):
    """The interface Chromium exposes for tags with no dedicated class: obsolete
    tags, custom element names and anything else unmodelled.  ``allow_new=false``,
    so the constructor throws; the host still builds instances through ``__init__``.
    """


#: The states ``button.type`` reports; a missing or unknown value is ``submit``.
_BUTTON_TYPES = frozenset(("submit", "reset", "button"))


@interface('HTMLButtonElement', 'HTMLElement')
class HTMLButtonElement(HTMLElement):
    """``<button>``: its name and value, and the state ``type`` reports."""

    def get_name(self): return self._attr("name")
    def set_name(self, value): self._set_string("name", value)
    def get_value(self): return self._attr("value")
    def set_value(self, value): self._set_string("value", value)
    def get_type(self):
        value = self._attr("type").lower()
        return value if value in _BUTTON_TYPES else "submit"
    def set_type(self, value): self._set_string("type", value)
    def get_form(self):
        from .html_form import HTMLFormElement
        form_id = self.attributes.get("form")
        document = getattr(getattr(self._services, "document_services", None), "document", None)
        if form_id and document is not None:
            owner = document.fn_getElementById(form_id)
            if isinstance(owner, HTMLFormElement):
                return owner
        node = self.parentElement
        while isinstance(node, Element):
            if isinstance(node, HTMLFormElement):
                return node
            node = node.parentElement
        return pyv8.Null

    def _activatable(self):
        """A submit or reset button clicks into its form; a plain one does nothing."""
        return self.get_type() in ("submit", "reset") and not self._bool("disabled")

    def _run_activation(self, event=None):
        """Submit or reset the form this button belongs to; this button is the
        submission's submitter, so its name and value join the entries."""
        form = self.get_form()
        if form is None or form is pyv8.Null:
            return
        if self.get_type() == "submit":
            from .activation import user_activated
            form._submit(self, user_activated=user_activated(event))
        else:
            form.fn_reset()


def _owning_table(row):
    """The nearest ``table`` ancestor of *row*, which is the table that counts it."""
    parent = getattr(row, "parentElement", None)
    while parent is not None:
        if isinstance(parent, HTMLTableElement):
            return parent
        parent = getattr(parent, "parentElement", None)
    return None


@interface('HTMLTableElement', 'HTMLElement')
class HTMLTableElement(HTMLElement):
    """``<table>``: the rows it owns -- those in its sections, in document order --
    but not the rows of a table nested inside it, which belong to that table."""

    def get_rows(self):
        view = getattr(self, "_rows_view", None)
        if view is None:
            view = self._rows_view = HTMLCollection(owner=self, getter=self._rows)
        return view

    def _rows(self):
        return [node for node in self._descendants()
                if isinstance(node, HTMLTableRowElement) and _owning_table(node) is self]


@interface('HTMLTableRowElement', 'HTMLElement')
class HTMLTableRowElement(HTMLElement):
    """``<tr>``: the cells it holds and where the row sits in its table."""

    def get_cells(self):
        view = getattr(self, "_cells_view", None)
        if view is None:
            view = self._cells_view = HTMLCollection(owner=self, getter=self._cells)
        return view

    def _cells(self):
        """A row's own cells: its ``td`` and ``th`` children, in document order."""
        return [child for child in self._children if isinstance(child, HTMLTableCellElement)]

    def get_rowIndex(self):
        """The row's position in its table's ``rows``, or -1 when no table owns it."""
        table = _owning_table(self)
        if table is None:
            return -1
        return next((index for index, row in enumerate(table._rows()) if row is self), -1)


@interface('HTMLTableCellElement', 'HTMLElement')
class HTMLTableCellElement(HTMLElement):
    """``<td>`` and ``<th>``: where the cell sits in its row."""

    def get_cellIndex(self):
        """The cell's position in its row's ``cells``, or -1 when no row holds it."""
        row = getattr(self, "parentElement", None)
        if not isinstance(row, HTMLTableRowElement):
            return -1
        return next((index for index, cell in enumerate(row._cells()) if cell is self), -1)


def _html_class(name):
    return interface(name, "HTMLElement")(type(name, (HTMLElement,), {"__module__": __name__}))


def _attribute(name, getter, setter=None):
    return AttributeSpec(name=name, getter=getter, setter=setter)


def _document_url(element):
    """The URL a relative attribute on *element* resolves against."""
    return str(getattr(getattr(element, "ownerDocument", None), "url", "") or "")


def _url_value(element, name):
    """A URL attribute's value, resolved against the element's document: the content
    attribute keeps what the page wrote, and absent or empty reads back as ``""``."""
    raw = str(element.attributes.get(name, ""))
    return urljoin(_document_url(element), raw) if raw else ""


def _set_url_value(element, name, value):
    element.attributes[name] = str(dom_string(value))


def _url_attr(name, getter, setter):
    """Declare a URL-reflecting attribute; pair it with the two helpers above."""
    return _attribute(name, getter, setter)


#: The elements a ``label`` may point at, by the HTML definition.
_LABELABLE_TAGS = frozenset(("button", "meter", "output", "progress", "select", "textarea"))


def _is_labelable(node):
    """Whether *node* is a labelable element, the kind a ``label`` can label."""
    if not isinstance(node, Element):
        return False
    if node.localName == "input":
        return str(node.get_type()) != "hidden"
    return node.localName in _LABELABLE_TAGS


@interface('HTMLLabelElement', 'HTMLElement')
class HTMLLabelElement(HTMLElement):
    """``<label>``: the control it labels (by ``for`` id, or its first labelable
    descendant) and the click it forwards to it -- a focus, then a click event."""

    def get_htmlFor(self): return self._attr("for")
    def set_htmlFor(self, value): self._set_string("for", value)
    def get_control(self):
        control = self._labeled_control()
        return pyv8.Null if control is None else control
    def _labeled_control(self):
        """The control this label labels, or ``None`` when it labels nothing."""
        target_id = self.attributes.get("for")
        if target_id:
            document = getattr(getattr(self._services, "document_services", None), "document", None)
            found = document.fn_getElementById(target_id) if document is not None else pyv8.Null
            return found if _is_labelable(found) else None
        return next((node for node in self._descendants() if _is_labelable(node)), None)
    def get_form(self):
        """The labeled control's form, so ``label.form`` answers what Chromium's does."""
        control = self._labeled_control()
        finder = getattr(control, "get_form", None)
        if finder is not None:
            form = finder()
            if form is not None and form is not pyv8.Null:
                return form
        node = self.parentElement
        while isinstance(node, Element):
            if node.localName == "form":
                return node
            node = node.parentElement
        return pyv8.Null

    def _activatable(self):
        """A label clicks into the control it labels."""
        return self._labeled_control() is not None

    def _run_activation(self, event=None):
        """Focus the labeled control and click it, the order Chrome uses.  The
        click repeats the label's own press (same coordinates, count and trust)."""
        control = self._labeled_control()
        if control is None:
            return
        from .activation import click_element, repeat_click
        from .focus import focus
        focus(control)
        if event is None:
            click_element(control)
        else:
            repeat_click(control, event)


@interface('HTMLImageElement', 'HTMLElement')
class HTMLImageElement(HTMLElement):
    def get_src(self): return _url_value(self, "src")
    def set_src(self, value):
        _set_url_value(self, "src", value)
        if self._services: self._services.resource_services.discover_element_resource(self)
    def get_alt(self): return self.attributes.get("alt", "")
    def set_alt(self, value):
        value = dom_string(value)
        self.attributes['alt'] = str(value)
    def _dimension(self, name):
        """One image dimension: the number the attribute starts with, else 0 --
        Chromium parses ``12px``/``12%`` as 12."""
        digits = ""
        for character in str(self.attributes.get(name, "")).strip():
            if character not in "0123456789":
                break
            digits += character
        return int(digits) if digits else 0
    def _set_dimension(self, name, value):
        # Not an unsigned long: a negative dimension stores as 0 rather than wrapping.
        self.attributes[name] = str(max(0, integer(value, signed=True)))
    def get_width(self): return self._dimension("width")
    def set_width(self, value): self._set_dimension("width", value)
    def get_height(self): return self._dimension("height")
    def set_height(self, value): self._set_dimension("height", value)
    def get_complete(self):
        """Whether the element is waiting on anything: no source, or a load that has
        settled either way -- Chrome answers false only while one is in flight.
        """
        if not self.attributes.get("src"):
            return True
        if not getattr(self, "_resource_started", False):
            return True
        return bool(getattr(self, "_image_complete", False))

    def _natural_size(self):
        """The size the loaded file declares, read once and kept.

        Chromium answers ``0`` until the image has settled, and the file's own header is what
        it answers with after: no decoded bitmap is needed for the question a page asks.
        """
        cached = getattr(self, "_natural_cache", None)
        if cached is not None:
            return cached
        data = getattr(self, "_image_bytes", None)
        if not isinstance(data, (bytes, bytearray)):
            return (0, 0)
        from .images import image_size

        size = image_size(data, getattr(self, "_image_type", ""))
        if size != (0, 0):
            self._natural_cache = size
        return size

    def get_naturalWidth(self): return self._natural_size()[0]
    def get_naturalHeight(self): return self._natural_size()[1]


@interface('HTMLScriptElement', 'HTMLElement')
class HTMLScriptElement(HTMLElement):
    __idl_overrides__ = {
        "text": {
            "getter": "get_textContent",
            "setter": "set_textContent"
        }
    }
    def _clone_shallow(self):
        clone = super()._clone_shallow()
        # HTML script cloning carries its already executed state to the clone.
        clone._script_executed = getattr(self, "_script_executed", False)
        return clone
    def get_src(self): return _url_value(self, "src")
    def set_src(self, value): _set_url_value(self, "src", value)
    def get_type(self): return self.attributes.get("type", "")
    def set_type(self, value):
        value = dom_string(value)
        self.attributes['type'] = str(value)
    def get_charset(self): return self.attributes.get("charset", "")
    def set_charset(self, value): self.attributes['charset'] = str(dom_string(value))
    def get_async(self): return "async" in self.attributes
    def set_async(self, value):
        value = boolean(value)
        if value: self.attributes["async"] = ""
        else: self.attributes.pop("async", None)
    def get_defer(self): return "defer" in self.attributes
    def set_defer(self, value):
        value = boolean(value)
        if value: self.attributes["defer"] = ""
        else: self.attributes.pop("defer", None)


#: The destinations ``link.as`` may report; anything else reads back as empty.
LINK_AS_VALUES = frozenset({
    "audio", "audioworklet", "document", "embed", "fetch", "font", "frame", "iframe",
    "image", "json", "manifest", "object", "paintworklet", "report", "script",
    "serviceworker", "sharedworker", "style", "track", "video", "webidentity", "worker",
    "xslt",
})


@interface('HTMLLinkElement', 'HTMLElement')
class HTMLLinkElement(HTMLElement):
    def get_relList(self):
        value = getattr(self, "_rel_list", None)
        if value is None:
            value = self._rel_list = DOMTokenList(self, "rel", LINK_REL_TOKENS)
        return value
    def get_href(self): return _url_value(self, "href")
    def set_href(self, value): _set_url_value(self, "href", value)
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value):
        value = dom_string(value)
        self.attributes['rel'] = str(value)
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = str(value)
    def get_as(self):
        value = str(self.attributes.get("as", "")).lower()
        return value if value in LINK_AS_VALUES else ""
    def set_as(self, value):
        value = dom_string(value)
        self.attributes['as'] = str(value)


@interface('HTMLAnchorElement', 'HTMLElement')
class HTMLAnchorElement(HTMLElement):
    """``<a>``: the URL decomposition members ``href`` carries.  Assigning one parses
    the current href, replaces that component and writes the result back, per URLUtils."""

    def _href(self):
        """The href every URL member below is derived from."""
        return _url_value(self, "href")

    def _parts(self):
        return urlsplit(self._href())

    def _set_component(self, **changes):
        self.attributes['href'] = urlunsplit(self._parts()._replace(**changes))

    def get_href(self): return self._href()
    def set_href(self, value): _set_url_value(self, "href", value)
    def get_origin(self): return origin_string(self._href()) if self._href() else ""
    def get_protocol(self):
        scheme = self._parts().scheme
        return scheme + ":" if scheme else ":"
    def set_protocol(self, value): self._set_component(scheme=str(dom_string(value)).rstrip(":"))
    def get_username(self): return self._parts().username or ""
    def set_username(self, value):
        self._set_credentials(username=str(dom_string(value)), password=self.get_password())
    def get_password(self): return self._parts().password or ""
    def set_password(self, value):
        self._set_credentials(username=self.get_username(), password=str(dom_string(value)))
    def _set_credentials(self, *, username, password):
        parts = self._parts()
        self._set_component(
            netloc=credentials_netloc(parts.hostname or "", parts.port, username, password))
    def get_host(self): return self._parts().netloc.rsplit("@", 1)[-1]
    def set_host(self, value): self._set_component(netloc=str(dom_string(value)))
    def get_hostname(self): return self._parts().hostname or ""
    def set_hostname(self, value):
        parts = self._parts()
        credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        port = ":" + str(parts.port) if parts.port is not None else ""
        self._set_component(netloc=credentials + str(dom_string(value)) + port)
    def get_port(self): return str(self._parts().port or "")
    def set_port(self, value):
        parts = self._parts()
        credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        value = str(dom_string(value))
        self._set_component(netloc=credentials + (parts.hostname or "") + ((":" + value) if value else ""))
    def get_pathname(self):
        parts = self._parts()
        return parts.path or ("/" if parts.netloc else "")
    def set_pathname(self, value): self._set_component(path=str(dom_string(value)) or "/")
    def get_search(self):
        query = self._parts().query
        return ("?" + query) if query else ""
    def set_search(self, value): self._set_component(query=str(dom_string(value)).lstrip("?"))
    def get_hash(self):
        fragment = self._parts().fragment
        return ("#" + fragment) if fragment else ""
    def set_hash(self, value): self._set_component(fragment=str(dom_string(value)).lstrip("#"))
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value): self.attributes["rel"] = str(dom_string(value))
    def get_target(self): return self.attributes.get("target", "")
    def set_target(self, value): self.attributes["target"] = str(dom_string(value))
    def get_relList(self):
        value = getattr(self, "_rel_list", None)
        if value is None:
            value = self._rel_list = DOMTokenList(self, "rel", HYPERLINK_REL_TOKENS)
        return value
    def fn_toString(self, *_extra): return self._href()

    def _activatable(self):
        """A link with an ``href`` navigates when clicked; a ``download`` link is
        declined (Chromium saves it, and this DOM has nowhere to save it)."""
        return bool(self._href()) and "download" not in self.attributes

    def _run_activation(self, event=None):
        """Follow the link via the location object, so a ``#fragment`` link stays a
        same-document navigation instead of a reload, as Chromium treats it."""
        href = self._href()
        if not href:
            return
        window = getattr(getattr(self._services, "document_services", None), "window", None)
        if window is not None:
            window.location.set_href(href)


@interface('HTMLStyleElement', 'HTMLElement')
class HTMLStyleElement(HTMLElement):
    def get_sheet(self):
        """The sheet this element owns, which is the one its document's list holds for
        it, and null while it is in no document: the list is what owns a sheet."""
        document = self.ownerDocument
        if document is None:
            return pyv8.Null
        for owner in document.get_styleSheets()._owners():
            if owner is self:
                return document.get_styleSheets()._sheet_for(owner)
        return pyv8.Null
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = str(value)
    def get_type(self):
        """Plain reflection, not a filtered one: whatever the page wrote reads back
        as it was written, and an absent attribute is the empty string."""
        return str(self.attributes.get("type", ""))
    def set_type(self, value):
        self.attributes["type"] = str(dom_string(value))


@interface('HTMLMetaElement', 'HTMLElement')
class HTMLMetaElement(HTMLElement):
    """The HTML ``meta`` element's reflected string attributes."""

    def get_name(self): return self.attributes.get("name", "")
    def set_name(self, value):
        value = dom_string(value)
        self.attributes['name'] = dom_string(value)
    def get_httpEquiv(self): return self.attributes.get("http-equiv", "")
    def set_httpEquiv(self, value):
        value = dom_string(value)
        self.attributes['http-equiv'] = dom_string(value)
    def get_content(self): return self.attributes.get("content", "")
    def set_content(self, value):
        value = dom_string(value)
        self.attributes['content'] = dom_string(value)
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = dom_string(value)
    def get_scheme(self): return self.attributes.get("scheme", "")
    def set_scheme(self, value):
        value = dom_string(value)
        self.attributes['scheme'] = dom_string(value)


@interface('HTMLTemplateElement', 'HTMLElement')
class HTMLTemplateElement(HTMLElement):
    def __init__(self, tag_name="template", services=None, **kw):
        super().__init__(tag_name, services=services, **kw)
        self.content = DocumentFragment(services=services)
    def get_content(self): return self.content

__all__ = [
    "Element", "HTMLElement", "HTMLUnknownElement", "HTMLImageElement", "HTMLScriptElement",
    "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement", "HTMLTemplateElement",
]
