import time
from pathlib import Path
import sys
from loguru import logger


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
sys.path[:0] = [str(WORKSPACE / '_pyv8'), str(WORKSPACE / 'pyv8c')]
from pyv8c import Browser

logger.add("akm.log", level="DEBUG", mode='w')
# logger.remove()

FRAME = 1 / 60


def human_move(page, x, y, *, frames=8, seed=None):
    """Approach ``(x, y)`` along a bowed path, one position a frame.

    Each frame is one hit test, which on a page this size can cost up to a second;
    raising *frames* buys a longer approach and pays for it here.
    """
    page.mouse.move(x, y, curve="human", steps=frames, duration=frames * 1 / 60, seed=seed)


def intercept(url, request):
    if request.get("resource_type") in {"image", "style"}:
        return False
    if '/akam/' in url:
        return False
    if ".js" in url:
        return False
    print(request.get("resource_type") , url)


def wait_for_cookie(browser_ctx, url, seconds=30):
    waited = 0.0
    while waited < seconds:
        if "~0~" in browser_ctx.cookies.header_for(url):
            print(browser_ctx.cookies.header_for(url))
            return True
        time.sleep(0.5)
        waited += 0.5
    return False


if "__main__" == __name__:
    st = time.time()

    browser = Browser(
        # stub_logging="verbose",
        # trace=True, traceable=True,
        resource_interceptor=intercept,
        # idl_path=Path(__file__).parent.parent.parent.joinpath("idls/145.0.7632.162.zip"),   # 换一份 IDL 文档；默认读包内 idl/idl.zip
        proxy="http://127.0.0.1:6696",
        http_engine='curl_cffi', http_options={"impersonate": "chrome150"},
        threaded_loaders=True,
    )
    context = browser.new_context()
    page = context.new_page()

    url = "https://www.bestbuy.com"
    # url = "https://www.koreanair.com"
    # url = "https://www.radissonhotels.com/zh-cn/"
    page.goto(url, wait_until="domcontentloaded")

    # 传感器看的是光标：逐帧靠近，落到终点后按住一会儿再松开（人手一次点击几十毫秒）。
    for x, y, seed in ((360, 220, 1), (700, 380, 2), (940, 470, 3)):
        human_move(page, x, y, seed=seed)
        # page.mouse.move(x, y, curve="human", steps=8, duration=8 / 60, seed=seed)
        page.run_until_idle(timeout=0)
    page.mouse.click(delay=0.07)
    page.run_until_idle(timeout=0)

    wait_for_cookie(context, url)

    browser.close()
    print(time.time() - st)
