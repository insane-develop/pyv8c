"""Canvas and OffscreenCanvas expose the surface attributes of their 2D renderer."""
from pyv8c import Browser


def test_default_context_attributes_and_image_pixel_format():
    browser = Browser(real_network=False)
    try:
        page = browser.new_context().new_page()
        page.set_content('<body></body>')
        results = page.evaluate('''[document.createElement('canvas'),new OffscreenCanvas(10,10)].map(canvas=>{
          const context=canvas.getContext('2d');const attributes=context.getContextAttributes();
          attributes.toneMapping.mode='changed';
          return [context.getContextAttributes(),context.getImageData(0,0,1,1).pixelFormat];
        })''')
        assert results == [[{'alpha':True,'colorSpace':'srgb','colorType':'unorm8',
                             'desynchronized':False,'toneMapping':{'mode':'standard'},
                             'willReadFrequently':False},'rgba-unorm8']]*2
    finally:
        browser.close()
