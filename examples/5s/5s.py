"""顺手一看：steamdb.info 这一轮有没有把 `cf_clearance` 给我们。

    python 5s.py            # 等 60 秒（goto 与收尾另算，整轮约 2 分钟），打一行 PASS / FAIL

判定只看一件事：上下文的 cookie 罐里有没有 steamdb.info 的 `cf_clearance`。
要知道"这一轮丢在哪一步"，用完整工具 `python script/5s.py --trace --seconds 90`
（交换表 + 罐 + 页面状态 + 同一行判定，trace 落到本目录的 5s.log）。
"""
import os
import sys
import threading
import time
from pathlib import Path

from loguru import logger
from pyv8c import Browser

logger.add(Path(__file__).with_name("5s.log"), level="DEBUG", mode="w")

URL = "https://steamdb.info/"
SECONDS = 60


def cookie_header(context):
    """罐里会为这个站点发出的 cookie 头；`cf_clearance` 也是一个 Set-Cookie。"""
    return str(context.cookies.header_for(URL) or "")


def wait_for_clearance(context, page, seconds=SECONDS):
    """让队列转起来，直到罐里出现它。realm 忙是挑战本身的一部分，所以只重试不抛。"""
    started = time.time()
    while time.time() - started < seconds:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001 - 忙/超时都在预期内
            pass
        if "cf_clearance" in cookie_header(context):
            return True
        time.sleep(0.5)
    return False


if "__main__" == __name__:
    began = time.time()

    browser = Browser(
        stub_logging="verbose",
        trace=True, traceable=True,
        proxy="http://127.0.0.1:7890",    
        http_engine='curl_cffi', http_options={"impersonate": "chrome150"},
        threaded_loaders=True,
    )
    context = browser.new_context()
    page = context.new_page()

    try:
        page.goto(URL, wait_until="domcontentloaded")
    except BaseException as error:  # noqa: BLE001 - 判定页会自己继续跑
        print(f"goto: {type(error).__name__}: {str(error)[:100]}")

    held = wait_for_clearance(context, page)

    print(f"cookies: {cookie_header(context)[:200] or '(none)'}")
    try:
        print(f"page: {page.url} / {page.evaluate('String(document.title)')[:60]}")
    except BaseException as error:  # noqa: BLE001 - 判定的另一半读不到也要照打
        print(f"page: unreadable ({type(error).__name__})")
    print("VERDICT: PASS -- cf_clearance held" if held
          else "VERDICT: FAIL -- no cf_clearance")

    #: 卡在挑战自己的循环里的 realm 不该留住进程：给它 10 秒，其余交给操作系统。
    closer = threading.Thread(target=browser.close, daemon=True)
    closer.start()
    closer.join(10.0)
    print(f"ran {time.time() - began:.1f}s")

    #: `os._exit` 跳过所有清理，所以上面每一行都得先落地（管道下 stdout 是带缓冲的）。
    sys.stdout.flush()
    os._exit(0 if held else 1)
