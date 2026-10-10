"""Which interface each HTML tag gets, read from Chrome 154.

Chrome answers an element whose local name has no hyphen in it with
``HTMLUnknownElement`` unless the name has an interface of its own, and this table
holds that second group; a hyphenated name is a custom element name and gets a
plain ``HTMLElement`` instead.  Only names are kept here, so the table can be read
beside the class registry without depending on it.
"""

TAG_INTERFACES = {
    "HTMLAnchorElement": ["a"],
    "HTMLAreaElement": ["area"],
    "HTMLAudioElement": ["audio"],
    "HTMLBRElement": ["br"],
    "HTMLBaseElement": ["base"],
    "HTMLBodyElement": ["body"],
    "HTMLButtonElement": ["button"],
    "HTMLCanvasElement": ["canvas"],
    "HTMLDListElement": ["dl"],
    "HTMLDataElement": ["data"],
    "HTMLDataListElement": ["datalist"],
    "HTMLDetailsElement": ["details"],
    "HTMLDialogElement": ["dialog"],
    "HTMLDirectoryElement": ["dir"],
    "HTMLDivElement": ["div"],
    "HTMLEmbedElement": ["embed"],
    "HTMLFieldSetElement": ["fieldset"],
    "HTMLFontElement": ["font"],
    "HTMLFormElement": ["form"],
    "HTMLFrameElement": ["frame"],
    "HTMLFrameSetElement": ["frameset"],
    "HTMLHRElement": ["hr"],
    "HTMLHeadElement": ["head"],
    "HTMLHeadingElement": [
        "h1", "h2", "h3", "h4", "h5", "h6",
    ],
    "HTMLHtmlElement": ["html"],
    "HTMLIFrameElement": ["iframe"],
    "HTMLImageElement": ["img"],
    "HTMLInputElement": ["input"],
    "HTMLLIElement": ["li"],
    "HTMLLabelElement": ["label"],
    "HTMLLegendElement": ["legend"],
    "HTMLLinkElement": ["link"],
    "HTMLMapElement": ["map"],
    "HTMLMarqueeElement": ["marquee"],
    "HTMLMenuElement": ["menu"],
    "HTMLMetaElement": ["meta"],
    "HTMLMeterElement": ["meter"],
    "HTMLModElement": [
        "del", "ins",
    ],
    "HTMLOListElement": ["ol"],
    "HTMLObjectElement": ["object"],
    "HTMLOptGroupElement": ["optgroup"],
    "HTMLOptionElement": ["option"],
    "HTMLOutputElement": ["output"],
    "HTMLParagraphElement": ["p"],
    "HTMLParamElement": ["param"],
    "HTMLPictureElement": ["picture"],
    "HTMLPreElement": [
        "listing", "pre", "xmp",
    ],
    "HTMLProgressElement": ["progress"],
    "HTMLQuoteElement": [
        "blockquote", "q",
    ],
    "HTMLScriptElement": ["script"],
    "HTMLSelectElement": ["select"],
    "HTMLSlotElement": ["slot"],
    "HTMLSourceElement": ["source"],
    "HTMLSpanElement": ["span"],
    "HTMLStyleElement": ["style"],
    "HTMLTableCaptionElement": ["caption"],
    "HTMLTableCellElement": [
        "td", "th",
    ],
    "HTMLTableColElement": [
        "col", "colgroup",
    ],
    "HTMLTableElement": ["table"],
    "HTMLTableRowElement": ["tr"],
    "HTMLTableSectionElement": [
        "tbody", "tfoot", "thead",
    ],
    "HTMLTemplateElement": ["template"],
    "HTMLTextAreaElement": ["textarea"],
    "HTMLTimeElement": ["time"],
    "HTMLTitleElement": ["title"],
    "HTMLTrackElement": ["track"],
    "HTMLUListElement": ["ul"],
    "HTMLVideoElement": ["video"],
    # Everything else Chrome answers with a plain HTMLElement.
    "HTMLElement": [
        "abbr", "acronym", "address", "article", "aside", "b", "basefont", "bdi", "bdo",
        "big", "center", "cite", "code", "dd", "dfn", "dt", "em", "figcaption",
        "figure", "footer", "header", "hgroup", "i", "kbd", "main", "mark", "nav",
        "nobr", "noembed", "noframes", "noscript", "plaintext", "rb", "rp", "rt", "rtc",
        "ruby", "s", "samp", "search", "section", "small", "strike", "strong", "sub",
        "summary", "sup", "tt", "u", "var", "wbr",
    ],
}

__all__ = ["TAG_INTERFACES"]
