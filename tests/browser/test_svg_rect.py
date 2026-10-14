"""The SVG legacy rectangle uses finite single precision, as Chrome does."""
from pyv8c import Browser
from pyv8c.web.dom.html_svg import SVGRect


def test_svg_rect_float_precision_and_invalid_values():
    browser = Browser(real_network=False)
    try:
        page = browser.new_context().new_page()
        page.set_content('<body></body>')
        page.realm.executor.call(lambda:page.context.define_global('__rect',lambda:SVGRect()))
        assert page.evaluate('''(()=>{const rect=__rect();return [rect.constructor.name,
          [-1,NaN,Infinity,.1].map(value=>{try{rect.width=value;return rect.width}
            catch(e){return [e.name,e.message]}})];})()''') == ['SVGRect',[-1,
              ['TypeError',"Failed to set the 'width' property on 'SVGRect': The provided float value is non-finite."],
              ['TypeError',"Failed to set the 'width' property on 'SVGRect': The provided float value is non-finite."],
              0.10000000149011612]]
    finally:
        browser.close()
