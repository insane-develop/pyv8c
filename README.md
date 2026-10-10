# pyv8c

## Native Bridge Protocol

Python 3.12+ is required. `Page.evaluate()` returns materialized data;
`Page.evaluate_handle()` returns primitives or live JS handles. Call
`handle.to_python()` explicitly for arrays and plain objects. Handles are
invalid after their Context closes. Internal bindings use the handle protocol
without implicit object enumeration.

`pyv8c` 是一个运行在相邻 `_pyv8` 原生扩展之上的、可控且可测试的 Chromium-like JavaScript 页面环境。
V8 负责执行 JavaScript；Python 负责页面、DOM/BOM、资源、事件循环、Worker、WebSocket、IndexedDB 和 DevTools 宿主行为。

它不是 Chromium 内核，也不承诺完整 Chrome 兼容性。当前实现重点是确定性的页面脚本执行、常用 Web API、独立 Realm、资源注入和可调试性。

## 快速开始

要求 Python 3.10+，并且运行环境能导入相邻仓库构建的 `pyv8` 扩展：

```powershell
Set-Location D:\src\pyv8env\pyv8c
$env:PYTHONPATH = 'D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
python -m pip install -e .
python -c "import pyv8; print(pyv8.__file__)"
```

最小示例：

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    page = browser.new_context().new_page("https://example.test/")
    page.set_content("<main id='app'>ready</main>")
    assert page.evaluate("document.querySelector('#app').textContent") == "ready"
    page.evaluate("globalThis.answer = 0; setTimeout(() => answer = 42, 0)")
    page.run_until_idle(timeout=1)
    assert page.evaluate("answer") == 42
finally:
    browser.close()
```

默认不会访问外网。使用 `resource_loader` 注入页面和资源，或显式启用真实网络后端。

请显式关闭 Page、BrowserContext 或 Browser。关闭的 Page 会从所属上下文移除，
关闭的 BrowserContext 会从 Browser 移除；页面返回的原生 JS 对象和函数随页面关闭
失效，后续访问会报错。关闭某个页面不影响其他页面的错误钩子。
独立资源清理失败时会报告异常，保留失败资源以便再次调用 `close()` 重试。

## 运行模型

```text
Browser
└── BrowserContext
    └── Page
        ├── PageServices       窄能力接口：Realm、任务、资源、文档、Worker、上下文
        ├── PageRealm           一个 V8 Context、BindingSession、owner executor
        ├── Window/Document     页面对象与 DOM 状态
        ├── Navigation/Lifecycle/Scripting/ResourceController
        ├── iframe Page         独立 Realm，通过 WindowProxy 访问
        └── WorkerRuntime       独立执行器和 Worker Realm
```

所有需要宿主能力的 Web API 只接收 `PageServices` 的窄 capability，不直接引用 `Page`、`Browser` 或平台控制器。

## Feature 架构

Web API 的装配只有一个概念：Feature。

- `FeatureSpec` 是不导入实现源码的静态声明，记录接口组、Window 槽位、依赖和实现模块。
- `FeatureCatalog` 在启动时建立不可变索引。
- `FeatureRuntime` 每个 Browser 独立，负责依赖解析、实现模块导入、接口类缓存、Realm 物化和 Window 值缓存。
- 实现模块显式导出 `FEATURE = FeatureImplementation(...)`，禁止通过 `vars(module)` 扫描注册。
- Core Feature 先加载源码，基础接口按 Realm 需要物化；IndexedDB、Navigator、WebSocket、Worker 等 Optional Feature 首次使用时才导入。
- 每个 Window 的 `navigator`、Storage、History、IndexedDB 等值由统一槽位缓存创建；`location` 是页面创建期值。

Feature 相关代码位于 `pyv8c/engine/features.py` 和 `pyv8c/web/features/`。接口基线只描述结构和绑定元数据，参数语义由具体 Web API 实现负责。

## 当前能力与边界

已覆盖：常用 DOM/BOM、URL、事件与集合、页面导航、资源 loader/interceptor、事件循环、Dedicated Worker、WebSocket、IndexedDB、V8 Inspector/DevTools、trace。

明确不覆盖：完整 CSSOM/布局/渲染、完整 Chrome 多进程模型、全部 Web API、完整 CDP Page/Network/DOM 域、多客户端协作和浏览器级安全沙箱。

## 测试与开发

```powershell
$env:PYTHONPATH = 'D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
python -m pytest -q
```

运行时架构和不变量见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，使用配置见 [docs/USAGE.md](docs/USAGE.md)，开发和验证流程见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

专题文档：

- [DevTools](docs/DEVTOOLS.md)
- [Trace](docs/TRACE.md)
- [资源加载与拦截](docs/resource-interception.md)
- [网络引擎与参数](docs/network-engines.md)
- [Window、iframe 与同源边界](docs/WINDOW_SECURITY.md)
- [IDL document](docs/idl-fields.md)
- [AI 接手开发规范](docs/AI_DEVELOPMENT.md)

## 相邻仓库

`_pyv8` 提供 Python/V8 原生桥接、Context/Isolate、Inspector 和 trace 原语。修改原生行为时，需要在 `_pyv8` 仓库单独运行其测试；本仓库只负责浏览器宿主和 Web API 实现。
