# 探针与骨架

## 0. 加一个探针（最常用）

探针 = 一个 JavaScript 表达式，返回 JSON 安全的数据。加进 `scripts/probes.py` 的 `PROBES`：

```python
SOMEPAGE = """<!doctype html><html><body>…</body></html>"""

PROBES = {
    # … 已有的 …
    "myname": {
        "note": "一句话：这个探针回答什么",
        "fixture": (SOMEPAGE, 0.6),      # 有 fixture 就能离线跑；不需要站点
        "probe": _probe("""
  var out = {};
  // _probe() 已经把 __view / __tags / __pick（元素读数工具）放进作用域
  out.body = __view(document.body);
  return out;
"""),
    },
}
```

- `_probe(body)` 会把共享的元素读数工具（`__view`、`__tags`、`__pick`）铺在前面，并整体包成
  一个表达式——`page.evaluate` 只接受表达式，不要写两条语句。
- 探针必须**容错**：被页面藏起来的节点上 `getBoundingClientRect()`、`attributes`、`children`
  可能不是对象；所有读取都 `try/catch`，失败给一个字符串标记（`'threw'`/`'missing'`）。
- 探针必须**可复现**：不要用 `Math.random()`、不要依赖当前时间（除 `timing` 那种本来就测时间的）。
- 用 `String()` 收口：pyv8c 会把 JS 的 `null` 交回成 pyv8 的 `Null`，直接 `json.dumps` 会失败。

跑它：`python scripts/harness.py --probe myname`（两引擎）或 `--side here`（只看 pyv8c）。

## 1. 直接对某个站点写临时探针

```python
"""一个页面、一个探针，两侧并排比。"""
import sys
sys.path.insert(0, r"<本 skill>\scripts")
from harness import chrome_path, diff, run_chrome, run_here

URL = "https://example.com/somewhere"
PROBE = """(function () {
  var node = document.querySelector('#app') || document.body;
  var rect = node.getBoundingClientRect() || { width: 0, height: 0 };
  return { box: Math.round(rect.width) + 'x' + Math.round(rect.height),
           children: node.children ? node.children.length : 'no-children',
           style: getComputedStyle(node).display };
})()"""

mine, viewport = run_here(URL, None, PROBE, 20, None)
theirs = run_chrome(URL, None, PROBE, 20, None, viewport)
diff(mine, theirs)
```

`run_here` / `run_chrome` 已经把"同一页面、同一视口、同一等待"处理好了：
先跑 pyv8c 拿到它报的 `innerWidth/innerHeight`，再用同一个视口开 Chrome。

## 2. 注入钩子（在页面脚本之前）

要观察页面自己做了什么，就在页面脚本前插一段。pyv8c 侧用 `resource_interceptor`
换掉主文档正文；Chrome 侧用 `page.add_init_script`。

```python
import curl_cffi.requests
from pyv8c import Browser
from harness import pump

HOOK = r"""
window.__watch = { seen: [] };
(function () {
  var probe = function (where) {
    try {
      var node = document.querySelector('#target');
      window.__watch.seen.push(where + ' ' + (node ? String(node.tagName) : 'absent'));
    } catch (e) { }
  };
  var wrap = function (proto, name) {
    var original = proto && proto[name];
    if (typeof original !== 'function') { return; }
    proto[name] = function () {
      var result = original.apply(this, arguments); probe(name); return result;
    };
  };
  wrap(Node.prototype, 'appendChild');
  wrap(Element.prototype, 'setAttribute');
  setInterval(function () { probe('timer'); }, 500);
})();
"""

done = {"document": False}
def intercept(url, request):
    if done["document"] or request.get("destination") != "document":
        return None
    done["document"] = True
    answer = curl_cffi.requests.get(str(url), impersonate="chrome")   # 以 Chrome 指纹取原文
    html = answer.text.replace("<head>", "<head><script>" + HOOK + "</script>", 1)
    return {"status": answer.status_code, "body": html, "url": str(url),
            "headers": dict(answer.headers)}

browser = Browser(stub_logging="off", resource_interceptor=intercept, threaded_loaders=True)
page = browser.new_context().new_page()
page.goto(URL)
pump(page, 30)
print(page.evaluate("({seen: window.__watch.seen})"))
```

钩子必须**原样转发返回值**，钩子内的异常一律吞掉，否则会把页面弄坏。

## 3. 引擎侧追踪（pyv8c 独有）

pyv8c 是 Python：直接包住内部方法就能看到"引擎以为发生了什么"。

```python
from pyv8c.platform.sessions import BindingSession      # 接口物化
from pyv8c.platform.resource_loading import ResourceController  # 资源与 iframe 上下文

log = []
for attribute in ("materialize", "_stage"):
    original = getattr(BindingSession, attribute)
    def traced(self, name, _original=original, _attribute=attribute):
        log.append((_attribute, str(name)[:40]))
        return _original(self, name)
    setattr(BindingSession, attribute, traced)
```

常用观察点：

| 想回答 | 包住 |
| --- | --- |
| 哪个资源被取、有没有用上 | `ResourceController.discover_element_resource`、`complete_document_resource` |
| iframe 有没有自己的浏览上下文 | `ensure_iframe_context`、`discard_iframe_context` |
| 哪个接口在哪次调用里被物化 | `BindingSession.materialize` / `_stage` / `class_config_for` |
| 某个节点在宿主侧到底是什么状态 | 直接读 Python 对象：`node.attributes`、`node.parentNode`、`node.ownerDocument`、`node._child_services` |

包法必须 `setattr` 成方法并用默认参数捕获 `original`（闭包陷阱）。

## 4. 读 `--trace` 日志

`python px3.py --trace`（案例里的 runner）或自己开 `trace=True` 会把每个 IDL 调用记进日志：
`[TOP] <getter|setter|method|descriptor|has>: Interface.member(args) -> value`。

- `待实现` 命中 = 页面撞到了未实现的成员（**这是最直接的"缺实现"证据**）；
- `has:` 表示页面在做特性探测（`'x' in y`），**不是调用**，别当成调用；
- `descriptor: … -> False` 表示宿主没有该自有属性；
- 用"某成员最后一次成功调用前后 N 行"来定位"何时开始出错"，比通读日志有效得多。

## 5. 网络/载荷对照（站点有 API 时）

先比**请求集合与顺序**，再比**体量与共同前缀**，最后才逐字段。两条经验：

- 体量差异要先排除"等待时长/采样次数不同"——同一条件各跑两次，看是否稳定；
- 载荷往往是站点自己的编码。定位编码器最省事的路径是**在页面里钩住发送前的明文**
  （钩 `JSON.stringify` / `Array.prototype.join` / `XMLHttpRequest.send` 并留一份快照），
  而不是去读混淆后的源码。

## 6. 结论模板（写进记录）

```markdown
### 症状短语

症状：一句话 + 两侧数字（哪条命令得到的）。

根因：哪一层、哪个函数、哪一行；用一句话说清机制。

修法：改了什么（文件 + 一句话），新加的常量/表放哪。

验证：最小探针两侧对齐的数字；真站上随之变化的量；回归结果。
```

## 7. 已知的坑

1. **页面忙时不能阻塞等待**：`run_until_idle(timeout=N)` 抛 `RealmBusyError`，用 `pump()`。
2. **无头 Chrome 不是参照**：UA、可见性、指纹项都不同。
3. **采样时间不同不是差异**：控件几何、载荷长度会随时间变，先在同条件下各跑两次。
4. **页面会隐藏自己的东西**：读不出来先做两侧同样的观测；`Chrome 也一样` 就不是缺陷。
5. **`evaluate` 只接受表达式**；**`Null` 不能直接 JSON 序列化**。
6. **资源 kind 要对齐**：登记资源用的 `kind` 与消费处认的 `kind` 必须一致
   （`<link rel=stylesheet>` 的 CSS 曾因两边不一致而从未被保存）。
7. **读 blob 源码**：`page.services.context_services.resolve_blob_url(url)` 返回
   `(源码字节, content-type)`；它**只在它自己的 context 里**认识 blob（`create_blob_url(blob, realm=…)`
   按 realm 登记），所以别的 realm 造的 blob 要么找该 realm 的 context services，要么进不了。
   用 `page.context`/`realm.context` 这类**原生 context** 调不到它（没有该方法）。
8. **进 worker realm**：`worker._runtime._realm.evaluate_script(...)`（`worker` 取 `page._owned.workers` 的成员）。
   `worker.services.realm_services.evaluate_script` 会答出**页面的**数据，是错误的一层；`worker._realm` 不存在。
   worker 里没有同步 `XMLHttpRequest`（Chrome 的 worker 有），所以别指望在 worker 里同步取自己的 blob。
9. **trace 要显式开、且走日志**：`Browser(trace=True, traceable=True)` 之外还要
   `context.trace = True`（`traceable` 是为"稍后再开"准备的）；`trace_callback=` 收不到事件，
   用 `logger.add(...)` 读 `pyv8c.engine.trace:log_trace` 的行。事件**带 realm 标签**
   （`[TOP]`/`[FRAME]`），成员名、参数和返回值都在里面——定位"哪一行/哪个成员"最快的就是它。
   拿它和钩子里记的 `Date.now()` 对齐，就能只读报错前那一小段。
10. **别包装 `window.Function` / `window.eval`**：在 pyv8c 的页面里包装 `Function` 会让
   `page.goto` 卡住（`RealmBusyError: realm executor did not complete 'goto' within 30s`）；
   包装 `eval` 会改变失败形态（异常从"`Cannot read properties of undefined`"变成页面侧的
   `ErrorEvent.message -> 'Error'`），也就是钩子干扰了被测对象。
11. **包 pyv8c 的内部方法比包 JS 稳**：想在"页面造东西"时记录，直接包它的 Python 回调
   （例如 `TrustedTypePolicy.fn_createScript/fn_createScriptURL/fn_createHTML`）——不改 JS 行为、
   任何 realm 的策略都看得见；包 `window.eval`/`Function` 则会干扰被测对象（见第 10 条）。
12. **blob 要"它自己的 realm"才读得到，而且层级会往下掉**：`resolve_blob_url` 只在对应 context
   里认识 blob；页面造的 blob 页面能读，**frame 的 worker、worker 里的 worker 造的**就只能由那一层读
   （主文档 / frame / 两者的 worker 都不行，会 `DOMException` 或返回错的东西）。
   枚举层级：主文档 `services.context_services` → `page._owned.frames`（是子 `Page`）→
   `page._owned.workers` 与 `frame._owned.workers`（取 `worker._runtime._realm`）。再深就只能靠钩子。
13. **想读混淆代码时，有 AST 可用**：Node 24 自带，`acorn` 装到仓库外
   （`%TEMP%\<dir>` + `NODE_PATH` 指过去）即可，不必往仓库塞 `node_modules`。
   挑战脚本的混淆形状是"**数字键表 + 运行时绑定的解码器**"（`X[dec(table.key)]`），
   解码器常在形参上再赋一次（`dd = Q2`）并带自解码启动代码，**静态推不出来**；
   可行的办法是：用 AST 找到"表赋值"与"解码器绑定"的**精确范围**，各包一层"捕获后原样返回"，
   在 `vm` 里配吞掉一切的 DOM 假对象跑一遍，再用抓到的解码器逐键求值。
   两个坑：假 Proxy 对不存在的键会返回它自己（捕获要写进**沙箱里的真实对象**）；
   表与解码器要用**同一个标签**配对。
14. **反爬站点每次给的页面可能不是同一份**：Cloudflare 会换 ray id / widget id，
   偶尔连挑战样式都换（同一 URL 实测过 `45 对 29` 个元素）。比结构前先确认两侧元素数一致，
   否则比的是两份不同页面；要重复复现就抓一份标记做夹具，两侧喂同一份字节
   （`examples/5s/script/parity_dom.py fixture_diff`），这样差异只剩解析器。
8. **`待实现` 清单是一把梯子**：修掉一项，页面的脚本往往就往下走一步，清单里换成下一项
   （5s 案例：5 → 2 → 1 → 0，最后一项从 `createOffer()` 变成 `setLocalDescription()` 再清零）。
   所以"清单没清零"不等于没进展，要看**脚本走到哪一步**；反过来，清零也不等于通过。
9. **参照浏览器不能带自动化标记**：Playwright 默认传 `--enable-automation`，真 Chrome 也会被
   Cloudflare 拦住；去掉后同一台机器就能过关。任何"两边都过不去"的结论，先确认参照不是被自己的
   驱动标记拖住的（见 SKILL.md 的先决条件）。
10. **"看起来没反应"先看连接性判断**：ShadowRoot 的 `parentNode` 是 null，任何沿 `parentNode`
    上溯到 Document 的"是否已插入文档"判断，都会把影子树里的节点判成未连接——iframe 于是不导航。
    这类判断要用含影子树的根（`isConnected`），不要去数父链。
