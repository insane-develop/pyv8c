# 使用与配置

[返回文档索引](README.md)。本页对应 [Browser 实现](../pyv8c/platform/browser.py) 与 [Page 实现](../pyv8c/platform/page.py)，不假定与 Playwright 或 Chrome API 等价。

顶层只导出对象模型（`Browser`、`BrowserContext`、`Page`、`DevToolsHandle`、`DevToolsOptions`、`BrowserEnvironment`）、
可捕获的错误（`PageError` 家族、`DOMException`、`NetworkUnavailableError`）与网络扩展点（`NetworkBackend`、
`HttpTransport`/`WebSocketTransport`、`register_transport`/`register_ws_transport`/`transport_names`、
`NetworkRequest`/`NetworkResponse`）。引擎实现类从各自模块导入，例如 `pyv8c.network.cronet.CronetTransport`。

## 创建与关闭

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    context = browser.new_context()
    page = context.new_page()
    page.set_content("<p id='message'>hello</p>", url="https://example.test/")
    assert page.evaluate("document.getElementById('message').textContent") == "hello"
finally:
    browser.close()
```

Browser、BrowserContext、Page 均提供显式 close；不要假定实现了 Python with 协议。
Context 关闭会关闭其页面、DevTools 和网络客户端，并清理 Blob URL。
多个 Context 的 Cookie/Blob/网络会话独立；显式指向同一磁盘缓存目录时，缓存文件不随之隔离。

## Page API

| API | 语义 |
| --- | --- |
| `context.new_page(url="about:blank", trace=None, environment=None)` | 创建页面；非 about:blank URL 会触发 goto，失败后关闭该页面 |
| `page.set_content(html=空 HTML 文档, url="about:blank")` | 提交新文档并启动解析；省略 html 得到 `<!DOCTYPE html>` 开头的空 standards 文档；设置 URL 不等于从该 URL 下载 HTML |
| `page.goto(url, timeout=30, wait_until="load")` | 加载并导航，通常返回 Response；beforeunload 阻止时可返回 None |
| `page.evaluate(script, filename=None)` | 在 owner 线程执行 JS 源码；filename 用于脚本定位 |
| `page.run_until_idle(timeout=0)` | 驱动就绪任务；正 timeout 允许等待定时器与在途资源/网络/worker 工作，到期返回，不承诺所有工作完成 |
| `page.dispatch_event(type, *, target="window", selector=None, event_class=None, init=None)` | 宿主侧派发 trusted 事件；返回是否未被取消，详见下节 |
| `page.query_selector(selector)` | 返回宿主侧 DOM 节点句柄，未命中返回 None |
| `page.set_visibility(hidden)` | 切换文档可见性并派发 visibilitychange/ freeze / resume |
| `page.mouse.move(x, y, steps=None, *, duration=None)` | 移动指针并派发整串边界/移动事件，见下节 |
| `page.mouse.down(*, button="left", click_count=None)` / `page.mouse.up(...)` | 按下 / 抬起；一次按下抬起在最近公共祖先上产生 click |
| `page.mouse.click(x=None, y=None, *, button="left", click_count=None, delay=None)` | 给了坐标先移动，再按下抬起 |
| `page.mouse.dblclick(x=None, y=None, *, button="left", delay=None)` | 两次计数连续的点击，第二次之后追加 dblclick |
| `page.mouse.move_to_element(element, *, force=False, duration=None)` / `click_element(...)` | 按元素（而非坐标）移动 / 点击，中心被遮挡时报错 |
| `page.click(selector, *, button="left", click_count=1, force=False, delay=None, duration=None)` | 移到元素中心点击，含激活行为 |
| `page.dblclick(selector, *, button="left", force=False, delay=None, duration=None)` | 同上，双击 |
| `page.hover(selector, *, force=False, duration=None)` | 移到元素中心，元素保持 hover |
| `page.trace = True/False` | 切换该页面观察状态，需要创建时配置观察能力 |
| `page.net.fetch(url, *, method="GET", headers=None, body=None, credentials=None, timeout=30)` | 用页面自己的 `fetch` 发请求并等它落定；返回快照对象（见下节） |
| `page.net.fetch_async(...)` | 同上，返回 `concurrent.futures.Future`，不阻塞页面 |
| `page.net.xhr(url, ...)` / `page.net.xhr_async(...)` | 用页面自己的 `XMLHttpRequest`，同样分别返回快照对象 / `Future` |
| `page.close()` | 关闭页面及其拥有的资源 |

`goto.wait_until` 支持 `commit`、`domcontentloaded`、`load`，以及内部接受的 None；不支持 networkidle。

重定向由 pyv8c 逐跳跟随，页面的 `fetch`/`XHR`/表单提交/`goto` 与自定义 `resource_loader` 走同一套规则：
301/302 把 POST 改成 GET 并丢弃 body，303 把除 HEAD 外的方法都改成 GET，307/308 保持方法与 body，最多 20 跳；
`Response.url` 是最终 URL、`Response.redirected` 在跟随过至少一跳时为 true，`Response.headers` 只暴露最终跳的响应头。
`fetch` 的 `redirect` 取 `follow`（默认）/`manual`（opaque redirect：status 0、type opaqueredirect、url 为请求 URL 且读不到任何头）/`error`（遇到 3xx 即网络错误）。
`location` 缺失或为空的 3xx 原样交付，无法解析为 http(s) URL 时报网络错误；导航的 `performance.navigation.redirectCount` 报告同源跳数，任一跳跨源则为 0。
规则表与逐条对照见 [资源加载文档](resource-interception.md) 与 [Chrome 对照用例](../tests/repros/test_redirects_vs_chrome.py)。
`set_content` 的默认 URL 是 about:blank，重复调用时需显式传 URL 才能保持预期 origin。
省略 html 得到一份空 standards 文档（`document.compatMode` 为 `CSS1Compat`）；传 `"<body></body>"` 这类不带 doctype 的片段则是 `BackCompat`，按需选择。
`evaluate` 不是自动等待 Promise 的异步求值器；可将完成结果写到 JS 全局，再推进队列并读取。

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.evaluate("globalThis.result = null; Promise.resolve(21).then(value => result = value * 2)")
    page.run_until_idle(timeout=1)
    assert page.evaluate("result") == 42
finally:
    browser.close()
```

默认 executor 自动消费事件循环；run_until_idle 是测试/等待辅助工具，并非每条语句之后都必须调用。
持续 interval 或永不结束的脚本不能被“空闲”调用可靠收敛；等待超时也不能抢占已开始的同步 Python 回调。

等待卡在哪、真正失败的是什么，都能读出来：

- `page.goto` 的超时是**导航自己的**：一条命令内部文档请求与生命周期等待共享 `timeout`，超时抛出 `TimeoutError`，文案里带等待的状态、文档请求耗时、以及**仍在加载**的资源（如 `still loading 1 script`）——即"卡在哪个脚本"。
- 子资源失败时，页面只听到 Chrome 的通用措辞，真正的原因写进日志：`resource failed: <url> (<异常>)`（DEBUG）。脚本元素也一样：`error` 事件里只有"脚本没到"这一件事，宿主的文本与异常对象都不在事件上（`event.error` 读出来是 `undefined`），原因进日志。JS 侧 `fetch`/XHR 的原因同样只进日志（`fetch failed: …`）。
- executor 的同步调用超时抛 `RealmBusyError`：`(command cancelled)` 表示命令还在排队、可安全取消；`(already running)` 表示命令已在 owner 线程上执行——它不会被打断，**结束时把结果或异常写进日志**（WARNING，形如 `'goto' failed 61.2s after it was submitted: NetworkError: …`）。所以"看起来卡死"时先看日志里那条迟到结果。
- `goto` 给 executor 留的宽限是 `NAVIGATION_GRACE`（5 s），保证报出原因的是导航本身而不是 executor。
- `PYV8C_CALL_TIMEOUT` 改的是**没显式传超时**的同步调用的默认预算（默认 60 s，`0`/负值不限）。

## 事件触发

页面加载生命周期事件由宿主按阶段派发：`readystatechange` → `DOMContentLoaded` → `load` → `pageshow`，导航与关闭时依次为 `beforeunload` → `pagehide` → `unload`；元素资源按 `loadstart` → `progress` → `load`/`error` → `loadend`，iframe 在子文档 load 后追加 `DOMFrameContentLoaded`。`hashchange`、`popstate`、`visibilitychange`、`freeze`、`resume` 由片段导航与 `set_visibility` 触发。

需要主动打事件时用 `Page.dispatch_event`，它始终在 owner 线程执行并把事件标记为 trusted：

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.set_content("<button id='go'>go</button>", url="https://example.test/")
    page.evaluate("document.querySelector('#go').onclick = e => window.__x = e.clientX")
    page.dispatch_event("click", selector="#go", init={"clientX": 7})   # 事件类按类型推断为 MouseEvent
    page.dispatch_event("custom", target="document", event_class="CustomEvent", init={"detail": {"n": 1}})
    page.set_visibility(True)
    assert page.evaluate("__x") == 7
finally:
    browser.close()
```

`target` 取 `"window"`（默认）、`"document"` 或 `query_selector` 返回的节点；`selector` 与 `target` 二选一，未命中抛 `NotFoundError`。`init` 是普通 dict，缺省时按事件类型补 `bubbles`/`cancelable` 默认值；`event_class` 可显式指定接口名，未知接口或非 `Event` 接口报 `TypeError`。

事件类本身（`MouseEvent`/`KeyboardEvent`/`PointerEvent`/`WheelEvent`/`FocusEvent`/`InputEvent`/`CompositionEvent`/`PopStateEvent`/`HashChangeEvent`/`StorageEvent`/`AnimationEvent`/`TransitionEvent`/`PromiseRejectionEvent`/`PageTransitionEvent`/`ProgressEvent` 等）只实现常用 init 成员与同名属性，其余成员仍由 stub 兜底；`BeforeUnloadEvent` 按 baseline 不可 `new`。

边界：`PromiseRejectionEvent`、`StorageEvent`、`AnimationEvent`/`TransitionEvent` 目前只提供构造与显式派发，不会自动触发（分别需要原生 Promise 拒绝回调、跨窗口 storage 广播与 CSS 动画引擎）；元素 `abort` 仅在显式取消路径可达。页面 `close()` 或导航重建文档时不会补发被取消资源的事件。

## 鼠标输入

三条路各管一段：`page.dispatch_event` 只发**一个** trusted 事件（事件类由你指定，不模拟输入）；`page.mouse` 是一台状态机，把位置、按键掩码与点击计数摊开；`page.click`/`hover`/`dblclick` 是选择器糖，负责把元素换算成坐标。

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.set_content('<a id=next href="#next">next</a>', url="https://example.test/")
    page.hover("#next")                     # 移到中心并保持 hover
    page.mouse.move(10, 10)                 # 也可以直接给坐标
    page.mouse.click(None, None, button="right")
    page.click("#next")                     # 移动 + 按下 + 抬起，含激活行为
    assert page.url == "https://example.test/#next"
finally:
    browser.close()
```

序列与 Chrome 153 实测一致（`tests/repros/test_mouse_events_vs_chrome.py` 把同一批场景在两个浏览器里各跑一遍逐项比对）：

| 动作 | 事件 |
| --- | --- |
| 移动到新元素 | `pointerout` → `pointerover` → `mouseout` → `mouseover` → `pointermove` → `mousemove` |
| 一次点击 | `pointerdown` → `mousedown` → `focus` → `focusin` → `pointerup` → `mouseup` → `click` |
| 连续第二次点击 | 上述序列的 `mousedown`/`mouseup`/`click` 报 `detail=2`，其后追加 `dblclick` |
| 右键 | `… → mouseup` → `auxclick` → `contextmenu`（左键才是 `click`） |
| `mousedown` 被 `preventDefault` | 只少掉聚焦，`click` 与激活行为照旧 |
| 目标被禁用（`disabled`） | 只剩 pointer 事件，无 `mousedown`/`mouseup`/`click`、不聚焦 |

`pointerenter`/`pointerleave`/`mouseenter`/`mouseleave` 不冒泡，传播路径只有元素本身（祖先的捕获监听看不到它们，与 Chrome 相同）。`click` 的目标是**按下与抬起的最近公共祖先**，所以在同一元素上按下抬起会落在它自己，跨元素拖动则落在共同祖先上。

`detail` 由计数决定：同元素、0.5 秒内、4 像素内的下一次点击计为同一次连续点击；`click_count=` 可显式指定，`mousedown`/`mouseup`/`click` 共用同一个计数。

`duration=` 与 `delay=` 把节拍交给调用方。不传时一次 `move(steps=N)` 的落点在同一循环里连着发（实测整批事件时间戳差 0–1 ms），而真 Chrome 对手工输入也按帧投递（实测相邻落点间隔 14–17 ms），所以「像不像真人」的差别主要在这里：

```python
page.hover("#next", duration=0.15)               # 接近用 150 ms
page.click("#next", delay=0.06)                  # 按下到抬起停 60 ms
page.mouse.move(400, 300, steps=12, duration=0.25)
```

节拍是宿主侧的**真实等待**：文档的时钟跟着真实时间走（`performance.now()` 与 `Event.timeStamp` 都如此），暂停是让落点真正分开的唯一手段；`page.run_until_idle(timeout=…)` 在页面空闲时会立刻返回，不能当节拍器用。每个落点又是一次全树命中测试（与 `document.elementFromPoint` 同一套几何），小页面约 10 ms、Radisson 那类真页面约 0.9 s，所以 `duration` 与采样数要按页面规模取舍。

`curve="human"` 让落点走一条弧线（二次贝塞尔 + 缓入缓出 + 小幅抖动），端点仍**精确**落在目标上；`seed=` 让同一条路径可复现，不传则每次自己弯一个方向。元素级入口同样接受它：

```python
page.hover("#next", curve="human", duration=0.3, seed=1)    # 像手一样绕过去再停住
page.mouse.move(900, 500, curve="human", seed=2)            # 采样数默认 24，可用 steps= 指定
```

事件的 `timeStamp` 读的是文档自己的时钟（与 `performance.now()` 同一时间轴，自 `timeOrigin` 起算）：脚本 `new Event(...)` 在构造那一刻取值，派发不再改。`performance.timeOrigin` 仍是 epoch 毫秒。

激活行为在 `click` 未被取消时执行：`<a href>` 走 `location`（因此 `#fragment` 是同文档导航）、checkbox/radio 翻转并触发 trusted 的 `input`/`change`、`<label>` 先聚焦再点它的控件、submit/reset 按钮按 `requestSubmit`/`reset` 走（含校验与 `submit` 事件，被取消则不提交；提交本身发出什么见下一段）。`<a download>` 不导航，`target="_blank"` 不新开页面。

`element.click()` 走同一套：派发一个 `isTrusted=false`、`detail=0`、坐标为原点的 `click`，再执行激活行为。所以 `a.click()` 会导航、`checkbox.click()` 会翻转，而被禁用的控件什么都不派发。`<label>` 的转发分两种：真实鼠标点击转发的是**它收到的那次按下**（保留坐标、计数与 trust），脚本 `label.click()` 转发的则是合成点击。

表单提交成的是 HTML 说的那个请求，而不是对 action 的一次普通导航：条目是控件的**树序**，且只含成功的控件——没有 `name` 属性、被禁用（含 `fieldset` 祖先）、未选中的 checkbox/radio、`reset` 按钮，以及**没有被按下**的提交按钮都不参与；`<select multiple>` 每个选中项一条、`<textarea>` 的换行在提交时归一为 CRLF。GET 把 action 的查询串**替换**成编码后的条目（一个条目都没有时也留一个孤立的 `?`），POST 保留 action 的查询串并把它放进 body；编码按 `enctype` 分 `application/x-www-form-urlencoded`（不编码的只有字母数字与 `*-._`，空格转 `+`，其余按 UTF-8 百分号编码）、`multipart/form-data`（`----WebKitFormBoundary` 加 16 位随机字符）与 `text/plain`（一条一行 CRLF）。请求头随之变化：`Content-Type`、非安全方法总带的 `Origin`、`Cache-Control: max-age=0`，以及**用户手势**带来的 `Sec-Fetch-User: ?1`——`page.click` 是手势，页面自己的 `form.submit()` 与 `button.click()` 不是，但后者照样把被点按钮的条目算进 body。以上取值与顺序逐项对照过本机 Chrome 153（`tests/repros/test_form_submission_vs_chrome.py`）。

焦点模型只做到够用的程度：`document.activeElement` 默认是 `body`，可聚焦的是表单控件（未禁用、非 `type=hidden`）、带 `href` 的 `a`/`area`、`iframe`，以及带 `tabindex` 的任何元素；`HTMLElement.focus()` 对别的元素无效（`div` 不会因此拿到焦点）。点击时聚焦最近的可聚焦祖先，找不到就清空焦点（原本聚焦的输入框会收到 `blur`/`focusout`）。没有 tab 顺序、`:focus` 样式与 shadow DOM。

命中测试由 `document.elementFromPoint`/`elementsFromPoint` 提供（此前是 baseline stub，调用返回 `undefined`）：`display:none` 整棵子树不参与，`visibility:hidden` 与 `pointer-events:none` 只跳过元素自身、子元素把任一属性改回来仍可命中，视口外的点返回 `null`。

边界与取舍：

- 几何只有 `dom.layout` 一套，它不实现定位（`position:absolute/fixed` 的元素按流式位置参与命中）、不实现 UA 样式表（表单控件的 border/padding/margin 不在盒子里）与滚动，且内联元素的宽度取容器宽。这些元素的坐标因此与 Chrome 有差，`page.click` 对它们可能需要 `force=True`；差异本身由语料库用例记录（`a_form_control_box_is_its_attribute_size`）。
- `page.click`/`hover`/`dblclick` 不等待：没有渲染循环，也就没有“元素稳定”可等。选择器未命中抛 `NotFoundError`，元素无盒子（未渲染）抛 `InvalidStateError`；中心被别的元素占住时默认拒绝并说明是谁，`force=True` 表示“照点”，此时事件落在遮挡者身上。提交请求同样是排队发出的，`page.click` 不等待它：要看结果用 `page.run_until_idle()` 或等 `page.url` 变化。
- 表单提交还不实现提交者对 action/enctype/method 的覆盖（`formaction`/`formenctype`/`formmethod`/`formnovalidate`，`<button>` 上也还没有这些属性）、`method=dialog`（没有对话框 UI，视为不提交）、`<input type=file>`（没有文件选择器，永远不产生条目）与 `input type=image` 的 x/y 坐标。
- 没有滚轮、键盘、触摸与拖放 API；`page.mouse` 不实现指针捕获与 `pointercancel`。
- `pointermove.getCoalescedEvents()` 给出该次输入里已合并的落点，末项是事件自身；一批落点（同一次 `move(steps=N)` 且无 `duration`）会累积到每个事件上，带节拍时每个落点自成一批。真 Chrome 在无合并时返回长度 1 的**等值副本**（不是同一个对象），我们不复制。`getPredictedEvents()` 恒为 `[]`，与 Chrome 的实测一致。移动类以外的指针事件（over/out/enter/leave/down/up）返回空列表，与 Chrome 相同。
- `click`/`auxclick`/`contextmenu`/`dblclick` 在这里是 `MouseEvent`，真 Chrome 派发的是 `PointerEvent`（因此没有 `pointerId`/`pointerType`/`isPrimary`，`e.constructor` 与 `instanceof` 也不同）。这条差异由 `tests/repros/test_mouse_events_vs_chrome.py` 的记录器捕获（对照时只对移动类事件比较合并表形状）。

## 文档写入

解析期调用 `document.write`（或 `writeln`）时，文本按**插入点**进入解析器：元素落在写脚本所在的位置，写入的 `<script>`
与文档中其他解析器插入脚本一样阻塞解析并执行，`writeln` 额外追加一个换行。

解析结束后再写入等价于 `document.open()`：当前文档内容被清空，写入的标记成为新内容，并在新的 `body` 内解析；
`Document` 对象、URL 与 `readyState` 都保持不变，导航生命周期（`unload`/`DOMContentLoaded`/`load`）不会重新派发。
没有浏览上下文的文档没有解析器，写入不产生效果。

## Browser 参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| resource_loader | None | 同步 Python 资源提供函数；离线测试首选 |
| real_network | True | 是否装配 http_engine / ws_engine 指定的引擎；False 表示这个 Browser 不联网 |
| http_engine / ws_engine | curl_cffi / websockets | 各给一个引擎：已注册的名字、现成对象（HTTP 要有 request_sync，socket 要有 websocket）或工厂（每个 Context 调一次）；None 表示该角色不装配 |
| http_options / ws_options | None / None | 分别交给两个引擎的参数；引擎不认识的键在创建 Context 时报错 |
| proxy | None | 出口代理 `http://[user:pass@]host:port`；None 与 `""` 都是直连（不看环境变量），可被 `new_context(proxy=...)` 覆盖，详见「网络代理」 |
| resource_interceptor | None | 主动跳过/替换请求，区别于被动 trace |
| threaded_loaders | False | 让 resource_loader / resource_interceptor 在 Browser 的有界线程池上执行：慢回调不再冻结 realm，代价是这种回调不得触碰页面 |
| cache / cache_dir | True / None | 允许缓存；只有显式目录才持久化资源响应 |
| environment | None | 扁平点号键字典或不可变 BrowserEnvironment |
| trace / traceable | False / False | 初始观察状态 / 是否允许后续开启 |
| trace_callback | None | 共享事件回调；未提供时使用 Loguru 输出 |
| interface_baseline_path | None | 默认随包 ZIP，可指定 compact schema 6 JSON/ZIP |
| baseline_mismatch | warn | warn（默认）对实现与所选 baseline 的成员/接口差异记一次警告并跳过；error 改为当场失败，用来盯住自己的实现写错 |
| expose_unimplemented | True | exposure 计划是否包含没有手写实现的 baseline 接口 |
| stub_logging | off | 未实现成员日志：off 关闭（默认）、on 每个未实现成员报一次、verbose 额外报告占位 handler |
| interface_overrides | None | 用宿主函数回答 baseline 成员（见下节）；未知接口/成员、类型不符、只读属性配写入钩子等在创建 Browser 时报错 |

关闭 stub_logging 只关闭日志，不会让占位成员获得实现。
原生 binding 描述和已创建模板不是热更新配置；更换 baseline 或修改接口声明后应创建新的 Browser/Context。

## 接口成员钩子

有些成员实现成本太高，或暂时不打算实现。`interface_overrides` 让宿主函数直接回答它们，
既不碰实现类也不改 baseline，同一个实现类仍被别的 Browser 正常使用：

```python
from pyv8c import Browser

browser = Browser(stub_logging="off", interface_overrides={
    "AbortController": {"abort": lambda *_: "已接管"},
    "Navigator": {"hardwareConcurrency": lambda *_: 8},
    "HTMLInputElement": {"valueAsNumber": {"set": lambda self, value: None}},
})
try:
    page = browser.new_context().new_page()
    page.set_content("<input id=i>", url="https://example.test/")
    assert page.evaluate("navigator.hardwareConcurrency") == 8
    assert page.evaluate("new AbortController().abort()") == "已接管"
finally:
    browser.close()
```

- 被钩子回答的成员不再进真实实现，本来就是 stub 的也不再报「待实现」，钩子的返回值就是脚本看到的值。
- 参数与真实实现一致，由成员位置决定（写 `lambda *_: 值` 最省心）：原型/实例方法收到 `(receiver, *args)`，
  位于接口自身（静态）的方法只收到 `(*args)`，属性读取收到 `(receiver)`，属性写入收到 `(receiver, value)`。
- 写法：裸函数回答该方法／该属性的读取，要指名写入用 `{"get": …}` / `{"set": …}`；配了 `set` 的成员即可写（含实现类本来没有 setter 的属性）。
- 成员的位置（原型/实例/静态）、`length`、只读标记仍取自 baseline，原型链与 `instanceof` 不变。
- 严格校验：接口名不在 baseline、成员名不属于该接口、槽位与该成员类型不符、给 baseline 标为只读的属性配 `set`、
  传入 bound method（会丢掉 receiver）或不可调用的值，都在创建 Browser 时抛 `TypeError`。
- 不覆盖：构造函数（`new` 仍走真实实现）、接口常量、`named`/`indexed` 拦截器。
- 代价口径：钩子是替换 callable，调用期没有可测的额外开销（与同一成员未 hook 时在噪声带内一致）；编译期只在编译一个成员时查一次表，未配置钩子时那张表根本不存在，`Bindings.validate()` 全量编译与未引入该功能时同档。只有钩子每次返回一个新对象（如 dict）才产生与真实实现同源的开销。
- 覆盖在首次建模板前定型：同一 Browser 的所有 context/realm（含 worker 与 iframe）看到同一套钩子，与 baseline 一样不是热更新配置。

## 实现一个接口成员

实现类只写函数体，名字按约定：operation 用 `fn_<成员名>`，attribute 用 `get_<成员名>` 与 `set_<成员名>`。
基线的成员表决定装什么、装在哪儿、arity 与标志，`__idl_overrides__` 只在"要与基线不同"时才需要：

```python
@interface("Example", "EventTarget")
class Example(EventTarget):
    def fn_doThing(self, value=MISSING, *_extra):   # -> Example.prototype.doThing
        ...
    def get_state(self):                            # -> readonly attribute
        return self._state
    def set_state(self, value):                     # -> writable attribute
        self._state = str(value)
```

需要声明的情形只有四种：回调换了名字（`__idl_overrides__ = {"abort": {"callback": "fn_static_abort"}}`）、成员基线里
没有、要覆盖基线沉默的 `setter`/`length`/`replaceable`，以及常量与拦截器。别的一样都不写 —— 写了也不生效，
`python -m pyv8c.tools.idl_audit` 会指出当前哪些行在重述基线。

## 环境配置

环境是一份扁平的「点号键」配置：每个设置一个键（`viewport.width`、`client_hints.brands` …），
一份配置可以整体写出、读回、叠加，没有第二套嵌套记录类型。字段表见
[environment.py](../pyv8c/foundation/environment.py) 的 `FIELDS`，校验集中在构造时，报错会指明键名。

```python
from pyv8c import Browser, BrowserEnvironment

environment = BrowserEnvironment({
    "languages": ["zh-CN", "en"],
    "viewport.width": 800,
    "screen.width": 2560,
})
browser = Browser(environment=environment, stub_logging="off")
try:
    page = browser.new_context().new_page(environment={"viewport.width": 390})
    assert page.evaluate("[navigator.language, innerWidth]") == ["zh-CN", 390]
finally:
    browser.close()
```

| 入口 | 语义 |
| --- | --- |
| `BrowserEnvironment(data)` / `from_dict(data)` | 逐键校验；未知键、类型或取值不符时抛错并指明键名 |
| `overlay(data)` | 在自身之上叠加若干键；页面级覆盖走同一条路径 |
| `to_dict()` | 只含显式给出的键 |
| `resolved()` | 默认值与推导补齐后的完整键集 |
| `dumps()` / `loads()`、`to_file()` / `from_file()` | 带 `schema` 版本号的文本或文件往返；版本不符即报错 |
| `viewport` `screen` `geolocation` `connection` `battery` `storage` `bluetooth` `client_hints` `plugins` `webgl` | 分组视图，属性名与 Web API 取值一致 |

未给出的键取**随包的默认 profile**（`pyv8c/data/chrome-environment.json`，一份真实 Chrome 的采集结果），
其中 `app_version`、`language`、`client_hints.brands`/`mobile`/`platform` 会**跟随 `user_agent`** 重新推导，
所以换 `user_agent` 时这些值一起变；推导结果不写回显式集合，要钉住某一项就显式给出它。
其余高熵值（架构、位数、平台版本、完整版本列表）无法从 UA 推出，仍取 profile 或显式值。

分组视图的读法：`environment.storage.temporary.quota`、`environment.client_hints.platform`、
`environment.plugins[0].mime_types[0].type`。

## 从真实浏览器采集配置

`pyv8c/tools/collect_environment.py` 用调试端口启动本机已装的 Chrome（`PYV8C_CHROME` 可指向别的可执行文件），
在 localhost 这个安全源上读取浏览器自己能上报的每一项，再从**同一次会话**读出它实际装在 `window` 上的名字，
写出一个可直接加载的 profile；`tools/collect_environment.py` 是它的命令行壳：

```powershell
$env:PYTHONPATH = 'D:\src\pyv8env\pyv8c'
python tools/collect_environment.py                       # 写随包 profile（pyv8c/data/chrome-environment.json）
python tools/collect_environment.py --out -               # 写到标准输出
python tools/collect_environment.py --headed --window-size 1920,1080 --accept-lang en-US,en
python tools/collect_environment.py --profile "C:\Users\me\AppData\Local\Google\Chrome\User Data"
```

- 产物只含**真实读到**的键（字段表中 `collected=True` 的那批）：缺一个即报错并以非零码退出，不会静默回落默认值。
- 浏览器无法上报的项不进产物，只作为覆盖项存在：`geolocation.*`（权限与坐标）、`viewport.mobile`、
  `viewport.touch`、`connection.type`、`connection.downlink_max`、`storage.persistent.*`。
- 产物带 `schema` 与 `source`（浏览器版本、可执行文件、平台、采集时间、启动参数），
  `BrowserEnvironment.from_file(path)` 可直接载入。
- 产物还带 `bindings` 段（`keys` 与 `exposed`）：参照浏览器实际装到多少，接口装配用它收窄 baseline，
  不手写名单。它不属于环境键，`BrowserEnvironment` 读 profile 时跳过，`engine/idl.py` 单独读这一段。
  一次运行同时给出两份事实，所以两者不可能来自不同浏览器。
- 产物还带环境键 `extensions`：本机**已启用且公开了资源**的扩展，`id -> [{resources, matches}]`（Chrome 自己的
  `web_accessible_resources` 形状），被权限变更暂停（`disable_reasons` 非空）的扩展不在其中。页面无法枚举扩展，
  所以这一项由采集器从浏览器自己的 profile 读取：`--profile` 指向 user-data-dir 或其中某个 profile，默认用本机的。
  资源层用它回答页面发出的 `chrome-extension://` 加载，见 [资源加载、拦截与缓存](resource-interception.md)。
- 默认无头启动，UA 因而会是 `HeadlessChrome/…`；要正常 UA 加 `--headed`。
- 调试端口本身会让 `navigator.webdriver` 为真；采集器用 `--disable-blink-features=AutomationControlled`
  中和这项由自己引入的痕迹，该参数同样记录在 `source.arguments` 中，便于复核。
- `screen.*`、`viewport.*` 是采集那一刻显示器与窗口的值，`storage.*.quota` 随 profile 与磁盘变化。

`pyv8c/data/chrome-environment.json` 是随包的默认 profile，也是一份真实采集的样例，离线测试直接用它。

配置会校验并冻结；几何数值是环境模拟，不代表存在布局引擎。
配置中出现 `timezone` 也不能推断所有原生 Date/Intl 行为已切换。

## window.chrome

`window.chrome` 按 Chromium 的形态安装：可写、可枚举、不可配置的全局属性，值是普通对象
（`Object.getPrototypeOf(chrome) === Object.prototype`、`chrome.constructor === Object`），
own key 顺序为 `loadTimes`、`csi`、`app`，没有扩展时不提供 `chrome.runtime`；Worker 全局没有 `chrome`。

```python
page.evaluate("[typeof chrome, Object.keys(chrome), typeof chrome.csi]")
# ['object', ['loadTimes', 'csi', 'app'], 'function']
page.evaluate("chrome.csi()")
# {'startE': 1789462997343.7854, 'onloadT': ..., 'pageT': ..., 'tran': 15}
```

- `chrome.csi()`：`startE`、`onloadT`、`pageT`、`tran`（正常导航为 15），每次调用返回新对象。
- `chrome.loadTimes()`：13 个字段，`navigationType` 为 `Other`，`wasFetchedViaSpdy`/`wasNpnNegotiated` 为 true、
  `npnNegotiatedProtocol`/`connectionInfo` 为 `h2`，尚未到达的里程碑为 0。
- `chrome.app`：`isInstalled`、`getDetails`、`getIsInstalled`、`installState`、`runningState`，
  以及 `InstallState`、`RunningState` 枚举。
- `chrome.csi`/`chrome.loadTimes` 是与绑定同源的 native 函数：`String(chrome.csi)` 为
  `function csi() { [native code] }`，`chrome.csi.name` 为 `csi`。
- 时间以当前文档的导航起点为锚，`goto`/`set_content` 之后会重新计时；精度高于 Windows `time.time()` 的
  15ms 刻度，连续两次 `chrome.csi().pageT` 不相等。

## Navigator

`mediaDevices`、`permissions`、`mediaSession`、`serviceWorker`、`credentials`、`bluetooth`、`storage` 与 `javaEnabled()` 按 Chrome 的可见性安装：`mediaDevices`/`serviceWorker`/`credentials`/`bluetooth`/`storage` 只在安全上下文出现，`permissions` 与 `mediaSession` 任何上下文都有，Worker 里只有 `permissions` 与 `storage`；`javaEnabled()` 恒为 `false`。七个对象都是 `[SameObject]`，取值、拒绝错误与文案逐项对照过本机 Chrome 153（`tests/repros/test_navigator_members_vs_chrome.py`）。

- `permissions.query({name})` 返回 `PermissionStatus` 的 Promise，初值表按 Chrome 153 固化（多数 `prompt`，`clipboard-write`/`accelerometer` 等为 `granted`，`periodic-background-sync` 为 `denied`）；非枚举名抛 `TypeError`，`push` 拒绝 `NotSupportedError`；`geolocation` 跟随 `environment.geolocation.permission`。
- `mediaDevices.enumerateDevices()` 只有一个默认 `audiooutput`（设备名与 id 为空），`getSupportedConstraints()` 是 Chrome 的 36 项；`getUserMedia` 无参抛 `TypeError`、有参拒绝 `NotFoundError`。
- `mediaSession` 只做状态与动作转发：`metadata` 必须是 `MediaMetadata`，`playbackState` 的非法值被忽略，`setPositionState` 校验时长与位置；`metadata.artwork` 的 `src` 按文档 URL 解析，且每次读取返回新的冻结数组。
- `storage.estimate()` 取环境的配额与用量（`usageDetails` 为空对象），`persisted()`/`persist()` 为 `false`；`bluetooth.getAvailability()` 取 `environment.bluetooth.available`（默认 `true`）。
- `serviceWorker.register()` 会真的取脚本并按 Chrome 的规则校验（跨源、空 URL、非 JS 的 MIME 都拒绝 `SecurityError`），通过校验后拒绝 `NotSupportedError`：这里不安装 Service Worker。
- 三处需要用户或存储层才能完成的调用拒绝而不是等待：`getDisplayMedia()` 拒绝 `NotAllowedError`、`bluetooth.requestDevice()` 拒绝 `NotFoundError`、`storage.getDirectory()` 拒绝 `NotSupportedError`（Chrome 在这三处等选择器或返回 OPFS 句柄）。

## URL 与 URLSearchParams

`URL` 按 URL 标准解析与序列化组件，`URL.searchParams` 是同一个查询串的**活视图**：对它的每次改动都写回 URL（用与 `toString()` 相同的序列化），对 `url.search` 的赋值也立刻反映到视图。`URLSearchParams` 本身按 Chrome 153 逐项对齐：

- `delete(name, value)` 与 `has(name, value)` 的第二个参数可选：给了就只匹配同名同值的条目（`delete('a', 1)` 只去掉 `a=1`，`has('a', 2)` 在有 `a=2` 时才为真），**省略或传 `undefined`** 才算"没给"（`null` 是字符串 `"null"`，`delete('a')` 去掉同名的全部条目）。
- 构造可以是字符串（按 URL 标准解析：只去掉**一个**前导 `?`，`+` 是空格，非法的百分号序列原样保留）、另一个 `URLSearchParams`（复制它**当前**的列表）、`[name, value]` 对的序列或记录对象；序列里有非二元组抛 `TypeError`。
- 编码与表单同一套（`application/x-www-form-urlencoded`）：只保留字母数字与 `*-._`，空格写 `+`，其余按 UTF-8 百分号编码，所以 `~` 写成 `%7E`。
- `sort()` 稳定地按 **UTF-16 码元**比较名字；`forEach(callback, thisArg)` 的实参是 `(value, name, params)`、`this` 是 `thisArg`，并且和标准一样按索引遍历**活列表**——回调里删掉的条目会被跳过。
- `keys()`/`values()`/`entries()` 返回真正的迭代器：`next()`、`[Symbol.iterator]()` 返回自身、每次 `next()` 重读列表（所以是活的），`[Symbol.iterator]` 就是 `entries` 本身，`for..of`、展开与 `Array.from` 都按 Chrome 的行为工作。视图由绑定按 `iterate="__v8_iterate__"` 声明的**迭代源**安装（`IdlSpec.iterate`，宿主只回答"第 N 项是什么"），所以它们是 `[native code]` 的原生函数，Worker 全局同样有。

已知缺口：`new URL('http://h/p?')` 会丢掉空查询串的 `?`（URL 解析器不保留"有查询串但为空"的状态，与这里的视图行为无关）。

## 网络与资源

`page.net` 是页面的网络入口，两种语义各一条路。`page.net.fetch` / `page.net.xhr` 在页面里调用 JS 的 `fetch` / `XMLHttpRequest`：命中**页面重写后**的实现（埋点、测试库常见），请求走页面的资源栈（CORS、拦截器、缓存），返回宿主侧快照对象（`status`、`status_text`、`url`、`headers`、`body`，以及 `.ok`、`.text`、`.json()`）；XHR 那份的 `headers` 为空，因为该实现没有 `getAllResponseHeaders`。
`page.net.get` / `page.net.post` 等相反：直接用 context 的传输，不经 JS、不做 CORS。需要哪种语义就选哪条路，但两者发出的头**一样**——都是本机 Chrome 会发的那一套（`Accept`、`Sec-Fetch-*`、`sec-ch-ua*`、`Referer`、`Origin` 等，取值来自 environment），见 [请求头对齐](request-headers.md)。
默认联网：`Browser()` 为每个 context 装配 curl_cffi（HTTP）与 websockets（WebSocket），两者来自 `[network]` extra，缺失时在创建 Context 时报错并指明要装什么。
离线测试用 `Browser(real_network=False)` 配 resource_loader，或直接用 set_content 提交本地 HTML。
http_engine / ws_engine 可改选其它已注册引擎（如 `"cronet"`，需装 `[cronet]` extra），也可直接给现成引擎对象或工厂，或传 None 关掉某个角色；http_options / ws_options 只对"按名字"的引擎生效，给现成对象时传会被拒绝。
选引擎、参数含义、校验规则和自定义引擎见 [网络引擎与参数](network-engines.md)；完整 loader 契约、响应格式、拦截和缓存风险见 [资源加载与拦截](resource-interception.md)。

## 网络代理

`Browser(proxy=...)` 是这个 Browser 所有 Context 的默认出口，`browser.new_context(proxy=...)` 可以按 Context 覆盖，所以同一个 Browser 里可以同时存在走代理与直连的会话（Cookie、缓存、引擎本来就各自独立）。

```python
from pyv8c import Browser

browser = Browser(stub_logging="off", proxy="http://user:pass@127.0.0.1:3128")
try:
    through = browser.new_context()                                # 走 Browser 的代理
    direct = browser.new_context(proxy="")                         # 显式直连
    another = browser.new_context(proxy="http://127.0.0.1:8080")   # 换一个出口
    print(through.proxy, direct.proxy, another.proxy)
finally:
    browser.close()
```

取值的三态：

| 值 | 含义 |
| --- | --- |
| 省略 / None | Browser 层：直连；Context 层：继承 Browser |
| `""` | 显式直连 |
| `http://[user:pass@]host:port`（也接受 `https://`） | 经该代理；URL 里的凭据会作为 `Proxy-Authorization` 发给代理 |

覆盖的流量：导航主文档、子资源、页面内 `fetch`/`XMLHttpRequest`（走资源栈）、`page.net` 的宿主请求，以及 `ws://`/`wss://` 握手；https 与 wss 经 `CONNECT` 隧道。`resource_loader`/`resource_interceptor` 直接应答的加载在传输层之前返回，因此不经过代理。

**未配置代理时不读环境变量**：按直连处理，即使进程里有 `HTTP_PROXY`/`ALL_PROXY` —— libcurl 与 websockets 客户端各自都会去读它们，pyv8c 会明确告诉两个引擎不要读。也不实现 bypass/no_proxy 列表：需要直连的 host 请在 `resource_interceptor` 里自行处理。

配置错误在最早的位置报出：

| 情况 | 行为 |
| --- | --- |
| 非 http/https（如 `socks5://`）、缺 scheme、缺主机、端口不可解析 | 构造 Browser 或 `new_context(proxy=...)` 时 `ValueError`（非字符串为 `TypeError`） |
| `real_network=False` 却给了代理 | 创建 Context 时 `TypeError`：没有引擎能承载它 |
| 引擎不声明支持代理（内置三个都声明了，自定义引擎默认没有） | 创建 Context 时 `TypeError`，指出该引擎不能被代理 |
| 同时给了 `proxy=` 与该角色自己的代理键（`proxy`/`proxies`/`proxy_auth`） | 创建 Context 时 `TypeError`，指出该保留哪一个 |

不做的部分：SOCKS4/5、PAC、页级覆盖、代理健康检查。自定义引擎要用代理，需在注册时声明 `proxy=True`（见 [网络引擎与参数](network-engines.md)）。

## DevTools

下面的片段需要人工打开打印的调试地址，不属于无人值守测试：

```python
from pyv8c import Browser, DevToolsOptions

browser = Browser(stub_logging="off")
try:
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page, options=DevToolsOptions(port=9005))
    print(handle.url)
    input("打开调试地址后按回车继续：")
    page.evaluate("globalThis.debugValue = 42", filename="example.js")
    input("调试结束后按回车关闭：")
finally:
    browser.close()
```

统一使用 `DevToolsOptions`：host="127.0.0.1"、port=0、wait_for_debugger=False、
pause_on_start=True、startup_timeout=None。默认不阻塞业务；完成握手后，在下一段宿主脚本第一条语句暂停一次。
每个 BrowserContext 只允许一个活动服务，保留单客户端模型；Browser 直接复用 `pyv8.devtools` 核心。
Handle 提供 close、wait_for_connection、wait_for_ready 和上下文管理；不会自动打开浏览器、打印地址或接管进程信号。
`wait_for_debugger=True` 显式等待启动释放；超时抛出 TimeoutError，不执行该脚本也不关闭页面。
同端口提供 /json、/json/list、/json/version。支持 iframe/Worker 自动附加和稳定 Target ID 重连。
支持 Runtime/Debugger 和部分 Target 命令，不提供完整 Page/Network/DOM 域。
不要将没有认证隔离保证的调试端口暴露给不可信网络。
旧 `start_paused=True` 改为 `DevToolsOptions(wait_for_debugger=True)`；详情见 [DevTools](DEVTOOLS.md)。

## 使用时的限制

- 不跨线程直接访问底层 Context 或操作持有的 JSObject；使用 Page 公开入口。
- 页面/Context 关闭后不要继续使用它的 JS 引用。
- 缺失成员可能只是 stub；对关键行为写断言，而不是仅检查 typeof。
- 主动读取 trace 事件里的对象引用可能产生 JS 副作用；日志使用安全 formatter，见 [TRACE](TRACE.md)。
- 这不是敌意代码安全沙箱；同源检查只覆盖已建模的浏览器行为。
