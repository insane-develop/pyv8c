# Browser DevTools

Browser 调试由 `pyv8.devtools` 提供协议、传输、会话及 Isolate 邮箱；
`pyv8c.platform.devtools` 只维护 Page/iframe/Worker 的宿主描述、父子关系和关闭回调。

```python
from pyv8c import Browser, DevToolsOptions

browser = Browser(stub_logging="off")
try:
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page, options=DevToolsOptions(port=9005))
    print(handle.url)
    input("打开调试地址后按回车继续：")
    if not handle.wait_for_ready(30):
        raise TimeoutError("DevTools 尚未完成调试握手")
    page.evaluate("globalThis.debugValue = 42", filename="example.js")
    input("调试结束后按回车关闭：")
finally:
    browser.close()
```

默认 host=127.0.0.1、port=0、wait_for_debugger=False、pause_on_start=True、startup_timeout=None。
首句暂停只作用于握手完成后的下一段宿主脚本，Console/初始化 helper 不消耗它。
默认每个目标只暂停一次，断线重连不重置；filename 控制 Sources 名称与 URL 断点匹配。
要关闭首句暂停，使用 DevToolsOptions(pause_on_start=False)。要等待客户端再启动业务，
使用 wait_for_debugger=True；startup_timeout 超时抛出明确 TimeoutError，不执行该脚本、不关闭页面。

Handle 的 url、host、port、close、上下文管理、wait_for_connection(timeout)、wait_for_ready(timeout)
与独立 Context 一致。两个 wait 返回 bool；socket 连接不代表 Debugger 已就绪。
Browser 已有执行器，直接调用 page.evaluate，不调用 handle.run；后者只用于独立 pyv8.Context。

## 子目标与关闭

同端口支持 `/json`、`/json/list`、`/json/version`，以及服务级、目标级 WebSocket 地址。
Target ID 随宿主存活，断线后可重新使用原地址；前端负责重新启用 Debugger 和恢复断点。
单客户端服务拒绝第二个 WebSocket（1013），不要向不可信网络开放无认证的监听端口。

flattened Target.setAutoAttach 按父 session 作用域管理直接子目标。新 iframe/Worker 先登记再运行启动脚本，
waitForDebuggerOnStart 决定启动等待。已有目标不会因为附加而回到启动等待。
父 session 的分离会清理子 session；父页面关闭会关闭其拥有的 iframe/Worker。

Page 和 iframe 共享 Isolate，但拥有不同 context group；Worker 使用自己的 Isolate。
暂停时只有该 Isolate 的暂停线程处理 Inspector 命令，不处理页面任务；其他 Isolate 继续独立执行。
断线/关闭先释放启动等待与暂停，再在合法线程销毁 Inspector，会话销毁完成后才停止网络服务。
关闭失败抛出异常。正常关闭幂等；关闭 Handle 不关闭页面。

## 迁移

- 原 host、port、startup_timeout 关键字放入 DevToolsOptions。
- 原 start_paused=True 改为 DevToolsOptions(wait_for_debugger=True)。
- 原 DevToolsServer、DevToolsTarget、DevtoolsDebugger 不再公开。
- 原独立 Context 的 callback=fn 改为 handle.run(fn)，地址由调用者 print(handle.url)。
- 不再隐式打印或接管进程信号，也不保留旧 WebSocket/队列实现。

不实现完整 CDP Page、Network、DOM 或多客户端协作。

## 验证入口

专项：`python -m pytest tests/browser/test_devtools.py tests/integration/test_race_regressions.py tests/integration/test_lifecycle_stress.py -q`。
真实 Chrome 前端验收：`python tools/devtools_chrome_smoke.py --chrome <chrome.exe>`。
工具使用临时用户目录和独立 Chrome 进程，不连接或关闭用户的现有浏览器。
回归入口见 `tests/browser/test_devtools.py`、`tests/integration/test_race_regressions.py` 和 `tests/integration/test_lifecycle_stress.py`。
