"""Imported nodes must be usable in replaceChild and owned by the destination."""
import pytest
from pyv8c import Browser


@pytest.fixture
def page():
    browser = Browser(real_network=False)
    try:
        page = browser.new_context().new_page()
        page.set_content('<body><div id="placeholder"></div></body>')
        yield page
    finally:
        browser.close()


def test_import_node_copies_into_destination_and_replaces_child(page):
    assert page.evaluate('''(()=>{
        const source=document.implementation.createHTMLDocument('source');
        source.body.innerHTML='<div id="origin"><span>child</span></div>';
        const original=source.body.firstChild;
        const shallow=document.importNode(original);
        const deep=document.importNode(original,true);
        const result=[shallow.outerHTML,deep.outerHTML,deep!==original,
          deep.ownerDocument===document,deep.firstChild.ownerDocument===document,
          deep.parentNode===null,original.parentNode===source.body];
        document.body.replaceChild(deep,document.getElementById('placeholder'));
        result.push(document.getElementById('origin')===deep,source.body.firstChild===original);
        return result;
    })()''') == ['<div id="origin"></div>', '<div id="origin"><span>child</span></div>'] + [True]*7


def test_import_node_rejects_invalid_nodes_and_unimportable_roots(page):
    assert page.evaluate('''(()=>{
        const shadow=document.createElement('div').attachShadow({mode:'open'});
        return [undefined,null,{},document,shadow].map(node=>{
          try { document.importNode(node); return 'no error'; } catch(e) { return e.name; }
        });
    })()''') == ['TypeError']*3 + ['NotSupportedError']*2


def test_import_fragment_and_executed_script_are_detached_and_inert(page):
    assert page.evaluate('''(()=>{
        window.calls=0;
        const script=document.createElement('script');script.textContent='calls++';
        document.body.appendChild(script);
        const fragment=document.createDocumentFragment();fragment.appendChild(script);
        const imported=document.importNode(fragment,true);
        const before=[imported.parentNode===null,imported.firstChild.ownerDocument===document];
        document.body.appendChild(imported);
        return [...before,calls,fragment.childNodes.length,imported.childNodes.length];
    })()''') == [True, True, 1, 1, 0]


def test_import_and_clone_preserve_svg_element_model_and_namespace(page):
    assert page.evaluate('''(()=>{
        const ns='http://www.w3.org/2000/svg';
        const svg=document.createElementNS(ns,'svg');
        svg.appendChild(document.createElementNS(ns,'g'));
        svg.firstChild.appendChild(document.createElementNS(ns,'text'));
        svg.firstChild.firstChild.textContent='sample';
        return [svg.cloneNode(true),document.importNode(svg,true)].map(copy=>[
          copy.namespaceURI,copy.nodeName,copy.firstChild.nodeName,
          copy.textContent,copy.ownerDocument===document,
          copy.firstChild.firstChild.namespaceURI]);
    })()''') == [['http://www.w3.org/2000/svg','svg','g','sample',True,'http://www.w3.org/2000/svg']]*2
