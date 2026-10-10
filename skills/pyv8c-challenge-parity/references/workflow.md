# 对照流程（详细）

## 0. 环境

```powershell
# 开发树里跑：pyv8c 源码目录与相邻的原生扩展都要在 PYTHONPATH
# （已按 README `pip install -e .` 装好时不需要）
$env:PYTHONPATH="<parent>\pyv8c;<parent>\_pyv8"

# 本 skill 的工具（站点无关）
python scripts/harness.py --list
python scripts/harness.py --probe ua --probe rect --probe style   # 先确认工具链通
```

- 源码包是 `pyv8c/`，本 skill 内提到的仓库路径都以 **pyv8c 仓库根**为基准。
- 参照 Chrome：本机装的 `chrome.exe`（Windows 常见位置
  `C:\Program Files\Google\Chrome\Application\chrome.exe`），**headed**。
  `harness.py` 会用 `CHROME_PATH` 或常见路径自动找到它，找不到退到 Playwright 自带 chromium
  并明确警告（那时 UA 与指纹都不同，结论要打折）。
- **视口必须一致**：`harness.py` 先跑 pyv8c、读它报的 `innerWidth/innerHeight`，再用同一视口开
  Chrome。手写运行器时也要照做——`pyv8c/data/chrome-environment.json` 里记的
  `viewport 1904×929` 是 1920×1080 的窗口减去浏览器边框，两侧不一致会让布局读数全部错位。
- 环境档案 `pyv8c/data/chrome-environment.json` 记录了 UA、语言、时区、屏幕、WebGL 等，
  pyv8c 从这里取值。判断"某项不一致"之前，先看档案里记的是什么。

## 1. 两侧运行器

最省事的是直接用 `scripts/harness.py`；要自己写时，遵循同一形状：

```python
# pyv8c 一侧
from pyv8c import Browser
browser = Browser(stub_logging="off", threaded_loaders=True)     # 走真实网络
page = browser.new_context().new_page()
page.goto(URL)
pump(page, 20)                                                   # 见坑 1
answer = page.evaluate(PROBE)                                    # 必须是表达式
browser.close()

# 离线最小对照：两边喂同一份页面
loader = lambda url, request: {"body": PAGE, "headers": {"content-type": "text/html"}}
browser = Browser(stub_logging="off", real_network=False, resource_loader=loader)
```

```python
# Chrome 一侧（Playwright sync API）
page = browser.new_page(viewport=VIEWPORT_FROM_PYV8C)
page.set_content(PAGE)                       # 或 page.goto + page.route 喂页面
page.wait_for_timeout(600)
answer = page.evaluate(PROBE)
```

要让 Chrome 也吃同一份"服务器"内容，用 `page.route("**/*", handler)` 按后缀应答。

## 2. 探针

- `page.evaluate` 只接受**表达式**：`(function () { … return out; })()`。
- pyv8c 会把 JS 的 `null` 交回成 pyv8 的 `Null`、`undefined` 成 `Undefined`；
  直接 `json.dumps` 会抛 `Object of type NullType is not JSON serializable`——探针里先 `String()`。
- 时间用 `performance.now()`（`Date.now()` 在同一毫秒内会掩盖差异）。
- 探针要容错：站点可能把节点换成"读什么都 `undefined`"的包装，`getBoundingClientRect()` /
  `attributes` / `children` 都可能不是对象。
- 差异定位到"哪个读数"就够了；探针的输出越是"一个字段一个值"，diff 越好读。

## 3. 引擎内打点（pyv8c 独有，非常有用）

pyv8c 是 Python，可以直接包住内部方法观察：

```python
from pyv8c.platform.resource_loading import ResourceController   # 资源发现/完成、iframe 上下文
from pyv8c.platform.sessions import BindingSession               # 接口物化

original = ResourceController.complete_document_resource
def traced(self, token, generation, response, error, handle=None):
    item = self.resources.get(token)
    node = item[1] if item else None
    print("complete", getattr(node, "localName", "?"), len(response.body_text() or ""))
    return original(self, token, generation, response, error, handle)
ResourceController.complete_document_resource = traced
```

| 想回答 | 包住 |
| --- | --- |
| 哪个资源被取、有没有用上 | `ResourceController.discover_element_resource`、`complete_document_resource` |
| iframe 有没有自己的浏览上下文 | `ensure_iframe_context`、`discard_iframe_context` |
| 哪个接口在哪次调用里被物化 | `BindingSession.materialize` / `_stage` / `class_config_for` |
| 节点在宿主侧到底是什么状态 | 直接读 Python 对象：`node.attributes`、`node.parentNode`、`node.ownerDocument`、`node._child_services` |

**"宿主侧正常、JS 侧读不出来"这种组合，通常就说明差异不在引擎，而在页面的反探测。**

## 4. 注入钩子（在页面脚本之前）

要观察页面自己做了什么：pyv8c 侧用 `resource_interceptor` 换掉主文档正文（顺便可用
`curl_cffi` 以 Chrome 指纹取原文），Chrome 侧用 `page.add_init_script`。骨架见
`probe-cookbook.md` 第 2 节。

## 5. 读 `--trace` 日志

**这一节是主路径** —— 详细方法见 `trace-parity.md` ✓，归类工具是
`scripts/trace_analyze.py` ✓（把"答 undefined 的读取/调用"与"抛错行"直接列出来 ✓）。

开 trace 后每个 IDL 调用都会记进日志：
`[REALM] <getter|setter|method|descriptor|has>: Interface.member(args) -> value`，
`REALM` 是 `TOP` / `FRAME` / `WORKER` ✓（**成片差异往往只在一个 realm** ✓）。

- `待实现` 命中 = 页面撞到未实现的成员（最直接的"缺实现"证据）；
- `has:` 是特性探测（`'x' in y`），**不是调用**；
- **只有带 `->` 的行才答了值** ✓ —— `setter:` 行没有 `->` ✗（把它当 `undefined` 会凭空造出几百条假差异 ✓）；
- 三条最值钱的筛法：**答了 `undefined` 的 getter** ✓、**带 `, error:` 的行** ✓、**本该出现却 0 次的 URL** ✓。

## 6. 网络与载荷对照

先比**请求集合与顺序**，再比**体量与共同前缀**，最后才逐字段。

- 体量差异要先排除"等待时长/采样次数不同"：同一条件各跑两次，看是否稳定；
- 载荷往往是站点自己的编码。定位编码器最省事的路径是**在页面里钩住发送前的明文**
  （钩 `JSON.stringify` / `Array.prototype.join` / `XMLHttpRequest.send` 留快照），
  而不是读混淆后的源码；
- 抓到明文之后，编码器就是"可验证的猜测"：用它把两侧的载荷都解出来，能解成合法结构才算对。

## 7. 定位改哪一层，以及验证

1. 症状先落到"哪一类读数"（布局 / 级联 / 资源 / 接口绑定 / 时间 / 定时器 / 事件）。
2. 在 `pyv8c/`（源码包）里找实现点，骨架见 `../SKILL.md` 的表。
3. 判断"缺实现"还是"语义不同"：前者日志里有 `待实现`、方法返回 `undefined`、接口不存在；
   后者两侧都能跑、数字/顺序/类型不同。
4. 改动尽量小，并在同一层加常量/表（例如 UA 相关就用 `TAG_DISPLAY` / `TAG_BOX_INITIALS`）。
5. 验证：**同一个探针**两侧对齐；真站上随之变化的量（页面走到了哪一步、拿到了什么值）；
   跑 `pytest tests -q --ignore=tests/repros` 与 `pytest tests/repros -q`。
   旧测试若断言了"旧行为"，先确认 Chrome 的实际行为，再把断言改成 Chrome 也成立的关系式
   （例如把 `div.offsetWidth === innerWidth` 改成 `=== document.body.clientWidth`），
   而不是直接改数字。

## 8. 坑（都被踩过）

1. **页面忙时不会空闲**：`run_until_idle(timeout=N)` 抛
   `RealmBusyError: realm executor did not complete …`。用非阻塞泵 + 截止时间
   （`harness.py` 的 `pump()`）。
2. **无头 Chrome 不是参照**：UA、可见性、指纹项都不一样。
3. **采样时间不同不是差异**：控件几何、载荷长度随时间/次数变化，先在同条件下各跑两次。
4. **页面会隐藏自己的东西**：读不出来先做两侧同样的观测，再下结论。
5. **`evaluate` 要表达式**、**`null` 不可直接 JSON 序列化**。
6. **资源 `kind` 要对齐**：登记资源用的 `kind`（如 `<link>` 记为 `"link"`）与消费处判断的
   `kind` 必须一致——`<link rel=stylesheet>` 的 CSS 曾因此从未被保存。

## 附：案例里更细的工具

`examples/px3/script/`（那轮 PX 验证码案例留下的四个工具）比本 skill 的 `harness.py` 更贴近
那个站点：控件时间线、控件子树、样式表、请求抓包与载荷解码、引擎侧追踪、注入钩子监视。
做同类站点时可以直接借用，也可以照它把通用 harness 再扩一层。
