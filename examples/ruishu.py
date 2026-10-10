import time
from loguru import logger
from pyv8c import Browser

logger.add("ruishu.log", level="DEBUG", mode="w")

def run(page, url):
    page.goto(url, wait_until="domcontentloaded")
    # page.goto(url)
    # page.run_until_idle(timeout=3)
    res = page.net.get(url)
    # res = page.net.fetch(url)
    # res = page.net.xhr(url)
    # print(res.text)
    logger.info(f"res: {res.status}, {len(res.text)}")


if "__main__" == __name__:
    st = time.time()

    def intercept(url, request):
        # print(request.get("resource_type") , url)
        if request.get("resource_type") in {"image", "style"}:
            return False
        if request.get("resource_type") == "script":
            if 'N7xexRXkVdNQ' in url:
                return None
            if 'fpqQrgG7L6po' in url:
                return None
            if '4QbVtADbnLVIc' in url:
                return None
            # https://lib.cqvip.com/N7xexRXkVdNQ/Y5oPTJBkyHck.cca831b.js
            # https://www.nmpa.gov.cn/fpqQrgG7L6po/eaKJbLE9bqof.e17ed02.js
            # https://www.cde.org.cn/4QbVtADbnLVIc/c.FxJzG50F.6152bb9.js?D9PVtGL=6152bb
            return False
        print(request.get("resource_type") , url)
        return None

    browser = Browser(
        stub_logging="verbose",
        # trace=True, traceable=True,
        resource_interceptor=intercept,
        threaded_loaders=True,
    )
    context = browser.new_context()
    page = context.new_page()
    run(page, "https://datasearch.nmpa.gov.cn/datasearch/home-index.html")
    # run(page,"https://www.cde.org.cn/main/xxgk/listpage/9f9c74c73e0f8f56a8bfbc646055026d")
    # run(page,"https://lib.cqvip.com/Qikan/Search/Index?from=index")

    # import json
    # print(json.dumps(page.evaluate("Object.keys(window)")))
    # print(page.evaluate("Object.keys(window).length"))
    # print(page.evaluate("Object.getOwnPropertyNames(window).length"))
    # print(page.evaluate("Object.getOwnPropertyNames(AbortSignal.prototype)"))
    # print(page.evaluate("Image.toString()"))
    # print(page.evaluate("HTMLImageElement.prototype"))
    # print(page.evaluate("navigation.entries()"))
    # page.evaluate("navigator.mimeTypes")
    # print(page.evaluate("NodeFilter.prototype"))
    # print(page.evaluate("NodeFilter.FILTER_ACCEPT"))
    # page.evaluate("navigator.plugins.refresh = '865'")
    # print(page.evaluate("new URL('https://www.koreanair.com/')"))
    # # print(page.evaluate("URL('https://www.koreanair.com/')"))
    # print(page.evaluate("navigator.plugins.refresh"))
    # print(page.evaluate("a=1;try {String(DOMException.prototype)} catch (error) {a=2};a==2"))
    # print(page.evaluate("(Object.getPrototypeOf(DOMException.prototype) === Error.prototype) == true"))
    # print(page.evaluate("e = new DOMException('');e.stack == undefined"))
    # print(page.evaluate("Error.isError(new DOMException('')) == true"))
    # print(page.evaluate("Error.isError(new DOMError('')) == false"))
    # print(page.evaluate("e = new DOMException('');e.__proto__"))
    # print(page.evaluate("DOMException instanceof Error"))
    # print(page.evaluate("c = document.createElement('canvas');d=c.getContext('webgl');d.getExtension('WEBGL_debug_renderer_info');"))
    # print(page.evaluate("""
    # i = document.createElement('input');
    # i.setAttribute('type', 'file');
    # i.setAttribute('capture', 'user');
    # i.capture
    # """))

    logger.info("browser close")
    browser.close()
    print(time.time() - st)
