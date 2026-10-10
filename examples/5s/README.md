# 5s：挑战 steamdb.info，拿到 `cf_clearance`

**判据**：上下文 cookie 罐里有 `steamdb.info` 的 `cf_clearance`，且页面是 SteamDB 本身而不是拦截页。

```powershell
cd pyv8env\pyv8c\examples\5s
$env:PYTHONPATH="D:\code\pyv8env\pyv8c;D:\code\pyv8env\_pyv8"

python 5s.py                                          # 简单入口：等 60 秒（整轮≈2 分钟），打一行 PASS/FAIL 并以 0/1 退出
python312 script\script/5s.py --trace --seconds 30 --timeout 220    # 判定入口：交换表 + 罐 + 页面状态 + PASS/FAIL，trace -> 5s.log
python312 script\chrome_side.py --seconds 30          # 参照侧：headed Chrome 的同口径交换表
python312 ..\..\skills\pyv8c-challenge-parity\scripts\trace_analyze.py 5s.log   # 归类：答 undefined 的读取 / 抛错
python312 script\secure_frame.py                      # 已修差异的两侧复现（本地，零第三方流量）

# 2026-10-10 新增的四件只读工具（本轮用来回答"谁在热读、谁不返回、缺什么"）
python script\dump_scripts.py --seconds 30            # 页面每个 realm 的脚本 -> script\cf_frame\（离线读）
python script\trace_reflect.py --seconds 30           # 每个被读名字的次数，以及读它的脚本/门（trace 回调当探针）
python script\slow_handlers.py --seconds 40 --over .8 # 超过阈值的回调 -> 源码（Function.prototype.toString）
python script\trace_gaps.py                           # trace 里几个尖锐差异的两侧最小复现（本地 fixture）
python script\frame_loop.py --seconds 90 --at 5000    # 在 realm 内部包 Reflect 的 getter，抓自旋栈并推回 Python
```

> 上面那段 `python312` 与 `D:\code\pyv8env` 是写这份文档的机器上的写法；本机（2026-10-10）是
> `D:\src\pyv8env` 且解释器叫 `python`（3.12.9）。命令本体不变，路径与解释器按当前 checkout 换。

方法用 `skills/pyv8c-challenge-parity/`：trace 归类 → 两侧最小复现/两侧交换表 → 修 + 守护测试 → 重跑比对。

## 两侧交换表（同一台机器、同一个站点）

`script/5s.py` 在 `CurlCffiTransport._call` 上挂观测（只说"服务器给了什么、我们收没收到"，不改行为）；
`chrome_side.py` 用 headed Chrome（`--enable-automation` 关掉、`AutomationControlled` 禁用）。
**Chrome 13.3 秒拿到 `cf_clearance`** ✓。差异按"只在一边出现"读：

| # | 交换 | pyv8c | Chrome | 判定 |
| --- | --- | --- | --- | --- |
| 1 | `GET https://steamdb.info/` | **403**（直接） | **307 → 403** | **差异**：少了那次 307 |
| 2 | `GET …/orchestrate/chl_page/v1` | 200 / 245 KB | 200 | 同 |
| 3 | `GET turnstile/v0/b/<id>/api.js` | 200 / 87 KB | 200 | 同 |
| 4 | `POST steamdb.info/…/fo/` | 200 / 107 KB | 200 | 同 |
| 5 | `GET blob:…`（291 B，worker relay） | 引擎内部（trace 有 `[Worker]` ✓） | 200 ×4 | 待对齐 |
| 6 | `GET …/turnstile/f/…` | 200 / 267 KB | 200 | 同 |
| 7 | `POST challenges…/fo/<id>` | 200 / 779 KB | 200 | 同 |
| 8 | `GET …/pat/<ray>` | **401** / 4 B（`J`） | **401** / 1 B | 状态同 ✓、**体不同** ⇒ 待查 |
| 9 | `GET …/ci/<ray>` | 200 / 32 KB PNG（**另一轮才有** ✓） | 200 / 1445 B | 每轮变体不同 ⇒ 只比集合 |
| 10 | `GET hagen…/i/<ray>` | 204 / 0 B | — | 我们多发 |
| 11 | `POST …/eb/<id>` ×2 | 200 / 5 B（`ok`） | — | 我们多发（疑为兜底） |
| 12 | 收尾 `POST …/fo/` 与再取 `GET …/` | — | 有 | 缺 |
| 13 | cookie | **0** | **1：`cf_clearance@.steamdb.info`** | 目标 |

### 请求数量与流程对照（工具化：`script/flow_compare.py` ✓）

同一台机器、同一站点、同窗口（90 s），两侧交换表对齐后的结果（2026-10-07 ✓）：

```
requests: pyv8c 11  /  chrome 15   (差 -4)

kind              pyv8c  chrome   说明
orchestrate           1       1   ✓
turnstile/api.js      1       1   ✓
turnstile/frame       1       1   ✓
fo@steamdb            1       2   数量不同（Chrome 收尾再发一次 ✓）
fo@cf                 1       2   数量不同
blob                  0       4   **不是差异** —— 引擎进程内供给 blob ✓，两侧都 `new Worker('blob:…')` ✓
pat                   1       1   数量同、状态曾不同（400 ✗ → 已修 ✓）
ci                    1       1   同上
i@hagen               1       0   只有我们 ✗
eb                    2       0   只有我们 ✗
root                  1       2   数量不同（Chrome 307→403 ✓，我们只有 403 ✗）

flow:
  pyv8c : root orchestrate api.js fo@steamdb frame fo@cf i@hagen pat ci eb eb
  chrome: root root orchestrate api.js fo@steamdb blob frame fo@cf blob blob blob pat ci fo@cf fo@steamdb
```

读法（skill 的规矩 ✓）：**先比集合、再看顺序** ✗ —— 挑战每轮变体不同 ✓，条数与顺序都不稳定 ✓；能稳定成结论的只有"某类请求只有一边有"与"同一请求两侧状态不同" ✓。这张表给出的两条正是这样被抓到的 ✓：`pat`/`ci` 的 400（已修 ✓）与收尾缺 `fo@steamdb`（未修 ✗）。

## 修掉的差异

### 沙箱帧被判成非安全上下文 ⇒ 31 个安全上下文成员读成 `undefined`（已修 ✓）

- **症状**：widget 帧里的 `navigator.userActivation`、`clipboard`、`credentials`、`mediaDevices`、`storage` 等 31 个名字全部答 `undefined`；页面自己在 20:54:19.886 读到 `HTMLDocument.URL === 'about:blank'`。
- **两侧证据**（`script/secure_frame.py`，本地一页两帧、零第三方流量）：

  | | `isSecureContext` | 五个 secure-only 成员 |
  | --- | --- | --- |
  | pyv8c（修前） | `false` ✗ | 全 `undefined` ✗ |
  | Chrome | `true` ✓ | 全 `object` ✓ |

- **根因**：帧文档是 `sandbox` 里的内联文档（`about:blank` / `about:srcdoc`），**地址本身没有 scheme**；`isSecureContext`、`navigator._secure()`、`window.crypto`/`caches`/`cookieStore` 都直接拿这个地址去判 ⇒ `false`。Chrome 的规则是**从创建者继承**，而仓里 `Page.origin` 早就这么做了（`origin_tuple(url, inherited=parent_page.origin)`）。
- **改法**：`web/bom/utils.py` 新增 `page_secure_context(page, url=None)` 与 `window_secure_context(window, url=None)`（沿 `Page.parent_page` 继承；window 一侧经 `PageServices._page_realm.page` 拿到 page —— `window.parent` 是 API 层访问器，沙箱帧给不出创建者 ✗），并把四处判定接到它们上；`platform/lifecycle.py` 与 `platform/navigation.py` 用 page 版（提交中的导航传新 URL）。
- **判据**：同一探针两侧逐字一致（`true` / `object` ✓）；真站 trace 的 `answered undefined` **31 → 4**，27 个 `Navigator.*` 全部退出（剩下 4 条是 `Window.event`、`Window.documentPictureInPicture` 等 Chrome 也如此的名字）。
- **回归**：`tests/browser/test_secure_context_frames.py`（2 条：https 页里继承为安全、http 页里仍不安全）；全量 `tests`（除 repros）**1082 passed, 1 skipped**，另修掉一处自己引入的回归——`get_userAgentData` 漏了 `worker=True`，worker 里 UA-CH 变成 `undefined`（由 `test_window_client_information.py` 抓住 ✓）。

### 窗口长度是变量：给足 90 秒后，挑战**提交成功并下发 `cf_clearance`**（新焦点 ✗）

同一份脚本、同一个站点，`--seconds 25/30` 都停在 `eb`（widget 显示 `cf-error`）✗；**`--seconds 90` 走到了最后一步** ✓：

```
POST https://steamdb.info/cdn-cgi/challenge-platform/h/b/fo/2823849642:…  -> 200 3491B
     set-cookie: cf_clearance                                     ← 服务器下发了 ✓
GET  https://steamdb.info/  -> 403 11004B  jar=1                  ← 重取时没带上它 ✗
… 于是又被拦一次（新的 chl_page/v1、api.js、fo）
cookies (1): cf_chl_rc_ni                                         ← 罐里只剩这个 ✗
VERDICT: FAIL -- no cf_clearance
```

- **症状**：`cf_clearance` 明确出现在该响应的 `Set-Cookie` 里 ✓，而运行结束时罐里只有 `cf_chl_rc_ni` ✗ ⇒ 下一次 `GET https://steamdb.info/` 不带它 ⇒ 403 ⇒ 再次进入挑战。
- **结论**：**不是"没收到"，而是"同时来两个、只留下一个"** ✗（`jar` 计数在同一时刻是 1，说明至少存过一个 ✓ ⇒ 需看每个响应的**原始 `set-cookie`**与罐的逐次变化）。
- **判据**：那一次 `fo` 响应之后，罐里必须同时有 `cf_clearance`（且 `GET https://steamdb.info/` 返回 200 而非 403）。
- **下一步（本案焦点）**：① 在运行脚本的观测里加"原始 `set-cookie` 头 + 每次变化的 cookie 名"；② 本地两侧复现：`127.0.0.1` 上开一个小服务，响应**一次给两个 `Set-Cookie`**（其中一个带 `Domain=./Path=/Secure/HttpOnly/SameSite=None`），看 pyv8c 罐与 Chrome 罐是否都收到两个；③ 修 `network/cookies.py` 的解析/存储。

### 空 cookie 罐曾以为是"没存 `Set-Cookie`"（撤回 ✓ —— 窗口太短而已）

25–30 秒的窗口里服务器一条 `Set-Cookie` 都没发 ✓，Chrome 跑完也只有 1 个（`cf_clearance` ✓）；
**给到 90 秒后发现服务器确实会发 `cf_clearance`** ✓ ⇒ 那时的"空罐不是缺陷"结论**只对窗口长度成立** ✗。

### 文档替换后仍在跑的任务，把引擎异常交到页面手里 ⇒ 站点捕获并上报（已修 ✓）

- **症状**（新 trace，90 s）：4 条 `, error:`，其中两条是**纯 Python 异常**（不是页面期待的 DOM 错误）：
  `[TOP] GPU.requestAdapter() -> error: <AttributeError>`、`[TOP] RTCPeerConnection.createOffer({…}) -> error: <AttributeError>`；
  紧跟其后 **3 条 `dispatch error on Window`**，再往后正是 **`POST …/eb/<id>` ×3（`chl_api_message`，上报）** —— 即**多发的请求就是站点上报我们的异常** ✓。
  trace 还显示站点把我们的异常对象**存进自己的变量**：`setter: Window.ihwO6 = <AttributeError>` ✓。
- **根因**（只读插桩打出 traceback，一次定位 ✓）：
  `pyv8c/foundation/scripts.py:74` → `AttributeError: 'NoneType' object has no attribute 'exec_js'`
  —— 文档被替换时 `RealmScripts.close()` 会把 `_context` 置 `None` ✓，而**旧文档的任务仍在跑** ✗ ⇒ 任何建 Promise 的调用（`promise_resolve`/`make_promise`）都在这里炸 ✗。
- **改法**：`foundation/scripts.py` 增加 `closed` 属性；`promise_resolve(value)` 在 realm 已关闭时**直接返回 `value`** ✓（与仓里既有约定 `realm is None ⇒ 返回 value` 一致 ✓）；`make_promise()` 返回 `(None, 无操作, 无操作)` ✓（已死文档里的 Promise 无人可观测 ✓）。**真正的根治**是"不要再跑旧文档的任务" ✓，已列为未解决项 ✓。
- **判据**：同一套扫描器跑新 trace ⇒ 两条 `AttributeError` **消失** ✓；`dispatch error` **3 → 0** ✓（我们的失败不再漏进页面 ✓）；`startswith` 之外的一切不变 ✓。
- **回归**：`tests/unit/test_realm_scripts_closed.py` ✓；全量 `tests`（除 repros）**1083 passed, 1 skipped** ✓，其中 1 个 failed 是既有的**帧提交时序抖动** ✓（隔离 **3/3 通过** ✓，与本改动无关 ✓）。

#### 深挖：为什么会是 `NoneType`（因果链完整 ✓）

| 步骤 | 证据 |
| --- | --- |
| 1. 文档被替换 ⇒ 新建 Window + `PageRealm`，**复用同一个 executor 与任务队列** | `platform/page.py:273` `_renew_document_realm` ✓（`attach_task_queue(self.tasks)` ✓） |
| 2. 随后关掉旧 realm ⇒ `scripts.close()` ⇒ **`_context = None`** | `platform/realms.py:266` ✓ + traceback `foundation/scripts.py:74` ✓ |
| 3. 提交时**只取消定时器**，队列本身"属于 page、比文档活得久" | `platform/navigation.py:59` `cancel_timers()` ✓ + 该处注释原文 ✓ |
| 4. 于是"被替换的那个 realm"仍被调用 ⇒ 任何建 JS 值的 helper 撞 `None.exec_js` ✗ | 修复前 trace 的两条 `AttributeError` ✓（一条 `[TOP]`、一条 `[FRAME]` ⇒ **两个不同的 realm** 都中招 ✓） |

**形状探针的负结果**（`script/late_document_work.py` ✓）：故意让一个慢响应在导航时仍在飞 ✓ —— 结果**没有**迟到续体（`window.name` 仍是 `start` ✓，无引擎错误 ✓）⇒ 导航会取消旧文档的 fetch ✓。而真站那两条调用在 `[TOP]`/`[FRAME]` 各一 ✓ ⇒ 迟到的工作来自**文档不拥有的东西**（widget 驱动的 worker 回包 ✓，它经 page 队列派发、比文档活得久 ✓）。复现这个形状是下一步 ✓（引擎侧的容忍已就位 ✓）。

#### 引擎改进：promise 不再靠执行 JS 来建（已落地 ✓）

- **原生侧**（`_pyv8`）：新增 `pyv8.PromiseResolver(context)` ✓ —— 直接包 `v8::Promise::Resolver`（`Resolver::New` / `GetPromise` / `Resolve` / `Reject` ✓，与 Blink 的 `ScriptPromiseResolver` 同一套 API ✓）；它**自己进入 realm** ✓（所以任意线程可建 ✓，`JSPromise` 做不到 ✓），**持有 Context** ✓，realm 关闭后每个方法给 `RuntimeError: the realm this PromiseResolver belongs to is closed` ✓。
- **引擎侧**（`pyv8c/foundation/scripts.py`）：`make_promise` / `promise_resolve` 改用原生 resolver ✓ ⇒ 那个文件**一行 JS 都不再评估** ✓（`_SOURCES` 与 `_helper` 全部删除 ✓）；`js_error_value` 只做"原样透传 + 解包 `JSException`" ✓（异常由 resolver 在结算时转成 JS 错误 ✓）。为了让普通 Python 异常**保留 message** ✓（旧的 `error_factory` 负责过这点 ✓），原生 settle 路径补了"异常 → `Exception::Error(str(exc))`" ✓（3 条 `test_redirects` 就是这么抓出来的 ✓）。
- **验证**：探针 `script/promise_resolver.py` ✓（页面看到 `[object Promise]` ✓、Python 侧 `resolve(42)`/`reject(...)` 都被页面观察到 ✓、关闭后是明确错误 ✓）；`tests/unit`+`tests/integration` 全绿 ✓；**全量 1084 passed / 1 skipped** ✓（唯一 error 是既有 devtools 抖动 ✓）。重跑新 trace：`, error:` **4 → 3** ✓（两条 `AttributeError` 保持消失 ✓）、`dispatch error` 3 → 0 → 1 ✓（剩下的那条紧跟 `ShadowRoot.appendChild` ✓）。

#### 清理：`RealmScripts` 已删除（它只剩薄壳 ✓）

原生 resolver 落地后，`RealmScripts` 的唯一存在理由（**按 realm 缓存编译好的 JS helper** ✗）就没了 ✓，剩下的全是转发 + 一份**重复状态**（`closed` 其实就是 `realm.context is None` ✓，而 realm 关闭时本来就记这一句 ✓）⇒ 删除 ✓：

| 原来 | 现在 |
| --- | --- |
| `RealmScripts.make_promise/promise_resolve` ✓ | `foundation/promises.py` ✓ 的无状态函数（吃 `context` ✓）+ realm 转发 ✓ |
| `RealmScripts.js_error_value` ✓ | 同上（**不需要 V8** ✓） |
| `RealmScripts.install_error_hooks/enqueue_microtask` ✓ | 直接调 `Context.set_error_hooks` / `Context.enqueue_microtask` ✓（原生就有 ✓） |
| `self.scripts` 的构造/关闭 ✓ | 删除 ✓；"已关闭"只有一个来源：`PageRealm.context = None` ✓ |

一个**必须保留**的细节 ✓：`ScriptController` 原本钉住 realm 的 `RealmScripts` ✓（这样"发起调用时属于哪个文档"不会因替换而改变 ✓）⇒ 现在它钉住的是 **realm 对象本身** ✓（`_realm` 属性 ✓，`clear_js_helpers()` 仍按原时机丢弃 ✓），迟到的调用因此落在**它当初那个 realm** ✓、并得到关闭后的兜底 ✓ —— 语义与之前逐字一致 ✓。

验证 ✓：无残留引用 ✓；`tests/unit`+`tests/integration` **724 passed** ✓；全量（除 repros）**1085 passed / 1 skipped** ✓（唯一 error 仍是既有 devtools 抖动 ✓）；重跑案例：`, error:` **3 → 2** ✓、`dispatch error` 3 ✓、`cf_clearance` 再次下发 ✓（jar 仍只留 `cf_chl_rc_ni` ✗，见下 ✓）。

#### 附带结论：promise 的原生做法（探针 `script/promise_native.py` ✓）

- pyv8 **原生已有** `v8::Promise::Resolver` 的用法 ✓：`src/handles/jspromise.cpp:74-105` 就是 `Resolver::New` → `GetPromise` → `Resolve/Reject` ✓（与 `v8-promise.h:32-52` 官方 API 一致 ✓）—— 但 `pyv8.JSPromise` 需要**当前线程上有激活的 V8 Context** ✓（实测：`RuntimeError: JSPromise requires an active V8 Context` ✓），所以引擎才走 `exec_js`（它自己会进入 context ✓）。
- **性能不是问题** ✓：现在的 helper 路线 **8.2 µs/次** ✓（2000 次 16.3 ms ✓）⇒ 值得改的理由是**形状与失败语义** ✓（这次那条 `NoneType` ✗、以 JS 源码当基础设施 ✗），不是速度 ✓。
- 落点：给 pyv8 加一个**持有 context 的 resolver 对象** ✓（`Resolver::New/GetPromise/Resolve/Reject` + "context 已释放" 的明确错误 ✓），引擎侧 `make_promise`/`promise_resolve` 即可不再评估 JS ✓；`Context.to_js` 可望顺带替掉两个 factory helper ✓（待在有 realm 的上下文里测 ✓）。

### 编码标签表按 WHATWG 补齐（已修 ✓）

- **差异**（两侧探针 `script/encoding_and_touch.py` ✓）：`new TextDecoder('iso-8859-1')` 我们抛 `DOMException` ✗，Chrome 正常 ✓。
- **改法**：`web/dom/encoding.py` 换成规范表（canonical 名 → Python codec + 别名 ✓）：`iso-8859-1`/`latin1`/`us-ascii` 都归 **windows-1252** ✓（所以 0x80 解出**欧元符号** ✓ 而不是 Latin-1 的控制码 ✓），另含 utf-16le/be ✓、windows-125x ✓、iso-8859-2…16 ✓、koi8-r/u ✓、gbk/gb18030/big5 ✓、euc-jp/euc-kr/shift_jis ✓、`replacement` 与 `x-user-defined` ✓（后两者规范就没有 codec ✓）。
- **判据**：两侧逐字一致 ✓ —— 8 个标签的 `encoding` ✓、`[0x80,0xE9,0x41]` 在 `iso-8859-1` 下解成 **`€éA`** ✓、未知标签仍 `RangeError` ✓。
- **回归**：`tests/browser/test_encoding_and_touch.py` ✓。

### 帧提交"就地执行"：`RealmBusyError` 的真正根因（已修 ✓）

- **证据（全线程转储 ✓，`script/5s.py` 的 `watch_executor()` ✓）**：一次运行里同一处卡了三次 ✓，两份栈合起来是完整因果链：

  | 线程 | 栈 |
  | --- | --- |
  | 帧自己的 executor 线程 | `set_content` → `commit_content` → `resume_parser` → 解析器 → **`execute_script` → `evaluate` → `_compiled_script`（要进 V8）** |
  | 父线程（当前） | `node_adopted` → `ensure_iframe_context` → `load_iframe_markup` → **`page.set_content` 等 60 s** |

  ⇒ 父线程**正在自己的 V8 调用里**（插帧 ✓）⇒ 它持有 isolate 锁 ✓，却跨线程死等帧线程 ✓；帧线程要编译/执行内联脚本 ⇒ **进不了 isolate** ✓ ⇒ 互等 60 s ⇒ `RealmBusyError (already running)` ✓✓。
- **改法**：`platform/page.py` 的 `Page.set_content` ✓ —— 当调用来自**正在运行的脚本**（`pyv8.current_context() is not None` ✓）时**就地提交** ✓，不再跨线程等待 ✓（`PageRealm.__init__` 早就有同款先例 ✓）。
- **判据**：同一份脚本重跑 ✓ —— `! realm busy` **3 → 0** ✓；`eb` 上报 **11 → 1** ✓（上报就是"引擎异常漏进页面"的指纹 ✓）。
- **回归**：`tests/browser` + `tests/unit` + `tests/integration` **1097 passed / 1 skipped / 0 error** ✓。

### 请求归属：worker 发的请求被算成宿主页的 ⇒ `pat`/`ci` 从 400 变回 401/200（已修 ✓）

- **症状**（两侧对齐器 `script/flow_compare.py`，2026-10-07）：形状完全相同的两个请求，Chrome 得 `401`（体 `J`）与 `200`（图），我们得 **`400 / 0B`** ⇒ widget 走不到最后一步。
- **两侧证据**：`pat` 的 URL 形状逐段一致（`[3,16,13,64,917]` vs `[3,16,13,64,895]` ✓）、无异常字符 ✓；唯一实质差异在**头**：
  | | `pat` 的 `Referer` |
  | --- | --- |
  | pyv8c | `https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/f/…` ✗（**宿主帧**的 URL） |
  | Chrome | **没有** ✓（只有 `sec-fetch-site: same-origin` ✓） |
- **根因**：widget 的 worker 由 `blob:https://challenges.cloudflare.com/<uuid>` 建 ✓（trace：`new Blob([...])` → `URL.createObjectURL` → `TrustedTypePolicy.createScriptURL` → `new Worker(<TrustedScriptURL>)` ✓），而 `platform/resource_loading.py:366` 一律用 **`current_url=page.url`** 算头 ⇒ 用**宿主页**的地址当 referrer ✗。Chrome 的规则是"请求归属发起方"，worker 的脚本 URL 是 `blob:` ⇒ **非 http(s) 不能作 referrer** ⇒ 不发 ✓。
- **改法**：`referer_for` 对非 http(s) 的 `current_url` 直接返回 `""` ✓；加载器改用 `request.get("initiator_url") or page.url` ✓；`web/workers/api.py:fn_fetch` 传 `initiator_url=self.script_url()` ✓（经 `fetch_like` ✓）。
- **判据**：重跑同一命令 ⇒ `pat` **400 → 401** ✓、`ci` **400 → 200** ✓（与 Chrome 同 ✓），且流程里**多出收尾的 `fo@cf`** ✓。
- **回归**：`tests/browser/test_worker_request_headers.py` ✓（worker 无 Referer ✓ + 页面自身仍有 ✓；防止"一刀切不发 referrer"✗）。

### XHR 字符串体缺默认 `Content-Type` ⇒ 服务端不回 `cf-chl-out`（已修 ✓）

- **症状**：收尾时 widget 读 `XMLHttpRequest.getResponseHeader('cf-chl-out') -> null` ✗，随后只发 `eb` 上报、不再继续 ✓。
- **两侧证据**（响应头见证，两侧同形）：
  | 同一请求 | 我们**发出**的头 | Chrome 发出的头 | 响应里的 `cf-chl-out` |
  | --- | --- | --- | --- |
  | `POST …/fo/`（提交） | `cf-chl=…; cf-chl-ra=0` ✓ —— **无 `Content-Type`** ✗ | `cf-chl=…; cf-chl-ra=0;` **`content-type=text/plain;charset=UTF-8`** ✓ | Chrome **有** ✓ / 我们 **无** ✗ |
- **根因**：规范规定 `XHR.send(<string>)` 在作者未设置时自动加 `Content-Type: text/plain;charset=UTF-8` ✓；引擎没加 ✗（仓里 `sendBeacon` 早已按这条规则做过 ✓ —— `web/bom/navigator.py:_beacon_body` ✓）。
- **改法**：`web/fetch/xhr.py` 新增 `_default_content_type(body)` ✓（Blob 自带 type ✓、空体无 ✓、其余按文本 ✓；作者设过就不动 ✓），`fn_send` 把它作为 `content_type` 交给加载器 ✓。
- **判据**：重跑 ⇒ 我们的 `fo` POST 带上 `Content-Type=text/plain;charset=UTF-8` ✓✓（与 Chrome 同 ✓），且响应里**出现 `cf-chl-out=`** ✓（服务端开始把它当文本 ✓）。
- **回归**：`tests/integration/test_xhr.py::test_send_gives_a_string_body_the_content_type_chrome_gives_it` ✓（字符串 ✓ / 作者自定义不动 ✓ / Blob 自带 ✓ / 空体无 ✓）。写这条时测试当场抓到我一个错 ✗：`Headers.fn_get` 缺省返回 `pyv8.Null` 而非 `None` ✓ ⇒ 判断恒真、所有 body 都不带类型 ✓。

### DOM/BOM 表面：把"方法与属性是否和 Chrome 一致"变成可复跑的一张表（新工具 ✓）

- **工具**：`examples/5s/script/surface_diff.py` ✓（探针是新加的通用 `surface` ✓，在 skill 的 `probes.py` ✓）。
  做法是**枚举**而不是手写名单 ✓：86 个持有者（`window`/`document`/`navigator`/`screen`/`location`/`history`/`performance`/`crypto`/`storage`/`indexedDB`/`canvas` 及其 `2d` 上下文/`iframe.contentWindow`/34 个构造器与其 `prototype` ✓），
  每个成员记**种类**（`get`/`set`/`fn:<arity>`/`value:<typeof>`/是否 `!enum` ✓），**只读 descriptor** ✓ ⇒ 不触发任何 getter ✓、不碰真站（离线 fixture ✓）。
- **判据**：`only-chrome`（页面能读到、我们读到 `undefined` ✗）、`only-here`（多余表面，**自身就是指纹** ✗）、`kind`（同名但种类/arity 不同 ✗）三个计数器 ✓。

```
                          only-chrome  only-here  kind
86 个持有者（初版 ✓）            10          4       9    ← 修掉两条"多余"后：only-here 4 → 0 ✓✓
98 个持有者（+6 命名空间 ✓）      10         50       9    ← 50 项全部归因于"6 个命名空间被做成函数"✗
98 个持有者（收窄面扩到 interface 位置后 ✓） 10  48   9    ← 其中 2 项（`CSS.animationWorklet`/`layoutWorklet`）已修 ✓
2046 个持有者（`--probe surface_wide` ✓） --  293 → 141 → 113   ← 宽扫才看得见的那几族：摘掉常量收割 293→141 ✓、修好 7 个接口的迭代表面 141→113 ✓（另 `kind` 11 = 已知的 `constructor` arity 族 ✓）
2046 个持有者（探针补上 4 个符号后 ✓）  74 → 60   113 → 116   ← `only-chrome` 里 23 项是符号 ✓；修掉 14 个 `@@iterator` ✓（`only-chrome` 74→60 ✓）、判据收紧后多发 3 个 ✗
```

> **为什么后来变成 98 个持有者** ✓：初版的名单里**没有** `CSS`/`GPU*` ✓，而它们正是 9 项 `kind` 差异里的 6 项 ✓ ⇒ 持有者名单本身就是一种"手写名单"✗，扩进去之后差异才**可归因** ✓（并且立刻多找出 `CSS.animationWorklet`/`layoutWorklet` 两条此前完全隐形的多余成员 ✓）。
> **`__missing`/`__error`/`__walkError` 是诊断、不是成员** ✗：早期把它们计入成员，六个函数形状的命名空间就报出 142 项"差异" ✓ —— 现在按持有者整体缺失单独报一行 ✓（`CSS.prototype` 在 Chrome 是 `undefined` ✓）。

### `Event.prototype.persisted`：多余的成员，方向相反但同样是指纹（已修 ✓）

- **症状**：`'persisted' in new Event('x')` 在我们这边为 `true` ✗，Chrome 为 **false** ✓（`new Event('x').persisted` 两侧都读得到值的地方不一样 ✓）。
- **两侧证据**（`surface_diff.py` ✓）：`Event.prototype` pyv8c 33 项 / Chrome 32 项 ✓，多出来的正是 `persisted` ✓；`CustomEvent.prototype`、实例 `event` 同因 ✓。
- **根因**：`pyv8c/web/dom/events.py` 把 `AttributeSpec(name="persisted", …)` 挂在 **`Event`** 上 ✗，而规范里它属于 **`PageTransitionEvent`** ✓ —— 该文件里 `PageTransitionEvent` 自己也声明了一份 ✓ ⇒ 是**重复声明**，不是缺实现 ✓。
- **改法**：只删 `Event` 上那一条 ✓（`PageTransitionEvent` 原样保留 ✓），代码里留注释说明判据来源 ✓。
- **判据**：`surface_diff.py` 的 `only-here` 从 4 降为 0 ✓；`tests/browser/test_surface_members.py` 守住三条：`'persisted' in Event.prototype === false` ✓、`typeof new Event('x').persisted === 'undefined'` ✓、`new PageTransitionEvent('pageshow', {persisted: true}).persisted === true` ✓（**行为没丢** ✓）。

### `Performance.navigation_id`：内部状态直接暴露给页面（已修 ✓）

- **症状**：`performance` 上多出一个**可枚举**的数字属性 `navigation_id` ✗（Chrome 的 `performance` 没有 ✓）。
- **两侧证据**：`surface_diff.py` 的 `performance` 行 34 vs 33 ✓、`only-here` 里点名 ✓。
- **根因**：`pyv8c/web/bom/performance.py` 的 `navigation_id = 1` 是**普通类属性** ✓ ⇒ 被 IDL 表面当成数据属性交给页面 ✗（同文件其余内部字段都带下划线 ✓ ⇒ 只有它漏了 ✓）。真正施法的是 `engine/binder.py` 里那段"收割非下划线 int 类属性当常量" ✗ —— **该段已在"全表面扫"一节里删掉** ✓，本条只是当时堵住的一处 ✓。
- **改法**：改名 `_navigation_id` ✓ 并同步 4 处 `self._navigation_id` ✓。
- **判据**：`only-here` 归零 ✓；守护测试断言 `Object.getOwnPropertyNames(performance)` 与 `Object.keys(performance)` 里都没有 `navigation_id` ✓，且 `performance.now()` 仍正常 ✓。

### 收窄面扩到 **interface 位置** ⇒ `CSS.animationWorklet` / `layoutWorklet` 消失（已修 ✓）

- **症状**：`CSS.animationWorklet`、`CSS.layoutWorklet` 在我们这边是可读的 getter ✓，Chrome 读到 `undefined` ✗ —— 页面因此看到**多余表面** ✓（这两条此前完全隐形 ✗，因为初版探针的持有者名单里没有 `CSS` ✓）。
- **两侧证据**：`surface_diff.py` 的 `CSS` 行 ✓（修前 `only-here` 10 ✓、修后 8 ✓，剩下的 8 条全是"它是函数"那一族 ✓）。
- **根因**：它们在基线里位于 **`location: interface`** ✓（`CSS` 的 71 个 interface 成员之一 ✓），而实测 profile 只测了 **window 自有名 + prototype** ✓ —— 代码里那句注释就是证词 ✓："Only the prototype was measured, not instance or interface-object members." ✗。基线"含全部 flag 状态、由实测收窄" ✓，于是这两条**没有收窄通路** ✗。
- **改法**：把测量扩到接口对象自身 ✓：`SURFACE_SCRIPT` 对每个 `window[X]` 读 `Object.getOwnPropertyNames(window[X])` ✓（命名空间不是函数 ✓，所以 `typeof === 'object'` 也读 ✓），profile 新增 `interface_gaps` 一节 ✓，`WindowSurface`/`InterfaceTable` 按**位置**分别收窄 ✓（`prototype_gaps` 只作用于 prototype ✓、`interface_gaps` 只作用于 interface ✓；`instance` 仍不测 ✓）。旧 profile 缺这节 ⇒ 完全不收窄 ✓（与"prototype 节缺席"同一套回退 ✓）。
- **判据**：重生成后 `CSS` 行 `only-chrome` **0** ✓、`only-here` **10 → 8** ✓、全局 `only-here` **50 → 48** ✓；`tests/unit/test_profile.py` 新增 `test_an_interface_gap_narrows_the_interface_object_only` ✓、`tests/unit/test_collect_environment.py` 新增"同一位置才互相比较" ✓、`tests/integration/test_environment_collection.py` 断言 `"animationWorklet" in interface_gaps["CSS"]` ✓、`docs/interface-baseline-fields.md` 就地改写 ✓。
- **同一机制还抓出另外 8 个同类接口** ✓（都是基线声明、参照 Chrome 不装的 interface 成员 ✓）：`SubtleCrypto.supports` ✓、`IdentityProvider.register`/`unregister` ✓、`URLPattern.compareComponent` ✓、`TrustedScript`/`TrustedScriptURL`/`TrustedHTML` 的 `fromLiteral` ✓、`LanguageModel.params` ✓、`HTMLUserMediaElement.isTypeSupported` ✓。
- **重生成配方** ✓（只动一个变量 ✓）：`python -m pyv8c.tools.collect_environment --headed --window-size 1920,1080` ✓ —— **必须 headed** ✓（无头跑会改掉 `user_agent`/`viewport`/`screen` ✓，实测 ✓）。与现行 profile 对比后，除新增的 `interface_gaps` 与 `source.collected` ✓ 只有 `connection.rtt/downlink/effective_type` 三个**活读数**不同 ✓ ⇒ 采集器输出即为真源 ✓。

### 全表面扫：把"手挑 40 个持有者"换成**遍历窗口暴露的每一个接口**（新工具 ✓）

- **工具**：`script/surface_diff.py --probe surface_wide` ✓ —— 同一份探针逻辑 ✓，但持有者是 `Object.getOwnPropertyNames(window)` 里的**每一个函数/对象**及其 `prototype` ✓ ⇒ 一次 **2046 个持有者** ✓（窄探针 98 个 ✗）。这正是 `CSS.animationWorklet` 上轮靠"想起来加上 `CSS`"才发现的教训 ✓：**手写名单只能确认已经想到的名字** ✗。
- **判据**：`only-here`（多余 ⇒ 指纹 ✗）、`only-chrome`（缺 ✗）、`kind` 三类计数 ✓，并按 **`名字+种类` 聚合** ✓ ⇒ 同一根因的差异自然聚成一族 ✓（全量明细落 `surface-surface_wide-*.json` ✓）。
- 首轮扫出 **45 种 / 293 次**多余成员 ✓，归成五族 ✓（下面三族已修/已定根 ✓）：

| 族 | 规模（首扫） | 根因 / 状态 |
| --- | --- | --- |
| **内部状态被当常量装出去** ✗ | 293 次里的 **208 次** ✓（`inputs`/`outputs` 46+46 ✓、`RULE_TYPE` 54 ✓、`channel_count` 4 ✓、`ELEMENT_NODE` 一族 3×18 ✓） | **已修** ✓：`engine/binder.py` 那段"任何非下划线 int 类属性都当 IDL 常量收割" ✓ —— 删掉 ✓（真常量由**基线**给 ✓）。判据：`only-here` **293 → 141** ✓、种类 45 → 40 ✓、全量 **1103 passed** ✓、且 `only-chrome` 里**没有任何全大写常量** ✓（即没有真常量被误删 ✓） |
| **命名空间是函数** ✗ | 8 种 × 6 持有者 ✓ | 未修 ✗（需原生层 ✓，见未解决表 ✓） |
| **迭代器方法装错接口** ✗ | 4 种：`HTMLCollection.prototype` 一派**多** 7×4 ✓、`CSSNumericArray.prototype`/`CSSTransformValue.prototype` 一派**少** 5×4 ✓ | 未修 ✗（一因两果 ✓：同一套 `entries/keys/values/forEach` 装到了集合接口上 ✓，Chrome 只在 `NodeList` 与那两个 CSS 序列上有 ✓） |
| canvas 2d 的 8 个属性 ✗ | 8 种 × 2 持有者 ✓（`CanvasRenderingContext2D.prototype` **与** `OffscreenCanvasRenderingContext2D.prototype` ✓） | 未修 ✗（基线缺 ✓，见未解决表 ✓） |
| 零星的 ✓ | `Response.json` ✓、`console.memory`/`createTask` ✓、`WebAssembly.compileStreaming`/`instantiateStreaming` ✓、`Iterator.includes/join/zip` ✓、`Date.prototype.toTemporalInstant` ✓、`window.Audio` 上的 `Object.prototype` 尾巴 ✓ | 未修 ✗，逐条已在 `surface-wide-report.txt` 与 JSON 里 ✓ |

### `Performance.navigation_id` 与 `AudioNode.inputs` 是**同一个根**（已修 ✓）

- **症状**：接口对象/实例上多出**可枚举**的内部状态 ✗（`navigation_id` ✓、音频节点上 46 处 `inputs`/`outputs` ✓、54 处 `RULE_TYPE` ✓）。
- **两侧证据**：`surface_diff.py --probe surface_wide` ✓ 聚合出"同一名字同一 `value:number` 出现在 N 个持有者里" ✓ —— 这种**跨持有者的重复**就是"引擎把实现细节当表面装出去"的特征 ✓。
- **根因**：`engine/binder.py` 曾把**任何**非下划线的 `int` 类属性当 IDL 常量收割 ✓（`constants.setdefault(field, ConstantSpec(...))` ✓）—— 所以 `inputs=1`、`channel_count=1`、`RULE_TYPE=1` 全被装上 ✗，而字符串属性（`channel_count_mode` ✓）不会 ✓ —— 这条**正好解释了**为什么只有 int 泄漏 ✓。
- **改法**：删掉那段收割 ✓（真常量由基线的 `constants` 与成员表提供 ✓）。上轮对 `navigation_id` 的改名仍然有效 ✓，但它当时只堵了一处 ✗ —— 根在这里 ✓。
- **判据**：`only-here` 293 → **141** ✓；`tests/browser/test_surface_members.py::test_implementation_state_is_not_an_idl_constant` 两半都守 ✓：`AudioNode`/`ScriptProcessorNode`/`AnalyserNode` 的自有名里**没有** `inputs`/`outputs`/`channel_count` ✓，而 `Node.ELEMENT_NODE === 1`、`Node.TEXT_NODE === 3` 且它仍是 `Node` 的自有属性 ✓。

### 迭代表面：**能迭代**与**有那四个具名方法**是两件事（7 个已修 ✓ / 5 个待修 ✗）

- **症状**：`HTMLCollection.prototype`（及其两子类 ✓）、`NamedNodeMap.prototype`、`Plugin`/`PluginArray`/`MimeTypeArray` 的 `entries`/`keys`/`values`/`forEach` 多出来 ✗；而 `CSSNumericArray`/`CSSTransformValue`/`CSSUnparsedValue`/`TimelineTriggerRangeList`/`XRInputSourceArray` 少同样四个 ✗。
- **两侧证据（这一步必须用符号探针 ✓）**：`surface_diff.py` 比的是 `Object.getOwnPropertyNames` ✓，**看不见 symbol** ✗ —— 它能说"多了四个名字" ✗，但不能说"是否可迭代" ✗。新工具 `script/iterable_probe.py` 直接读 `typeof proto[Symbol.iterator]` + 那四个名字 ✓，同一份页面跑两侧 ✓：**两侧都有 iterator** ✓，Chrome 只是没有那四个具名方法 ✓（连 `NamedNodeMap`/`PluginArray` 这类 legacy 集合也一样 ✓）⇒ 修法是"**收窄**"而不是"删掉迭代" ✓ —— 若凭印象删 ✗ 就会把真实的可迭代能力一起删掉 ✗。
- **根因**：当时 `array` 还是**位掩码** ✗，一个常量 `ITERABLE_FLAGS` 就把 `Symbol.iterator + entries + keys + values + forEach` 一起发布 ✓（`web/dom/utils.py` ✓），而 Chrome 按**每个接口**给不同的组合 ✓。`HTMLCollection`/`NamedNodeMap` 在 pyv8c 里**继承 `NodeList`** ✗（实现走捷径 ✗），而基线里两者的 `parent` 都是 **`None`** ✓（与 Chrome 一致 ✓）—— 父模板的位压不掉 ✓，实测"显式只声明 iterator 位"也无效 ✗。
- **改法**：把 `NodeList` 的**实现体**下沉成 mixin `_ListItems` ✓（`web/dom/collections.py` ✓），两个接口改继承它 ✓（`RadioNodeList` 是规范的 `NodeList` 子类 ✓ 保持不动 ✓）；各自声明 `array="iterator"` ✓（现在就是两个词之一 ✓）；并把原来"顺手继承来"的 `length` 补进各自 spec ✓。
- **踩到的坑（记下来 ✓）**：`ReadOnlyIndexedPropertyMixin` 自己也有 `_indexed_items`（`raise NotImplementedError` ✗）⇒ **基类顺序反了就会盖掉实现** ✗，症状是索引属性全炸 ✓（10 个测试同时红 ✓）；正确顺序是"实现类在前、拦截器 mixin 在后" ✓。改完 5 个失败收成 0 ✓。
- **判据**：`iterable_probe.py` 里那 7 个**两侧一致** ✓（原先是 7 个 `differs` ✓），而 6 个"本应五个全有"的（`NodeList`/`RadioNodeList`/`DOMTokenList`/`Headers`/`FormData`/`KeyboardLayoutMap` ✓）**改动前后都一致** ✓；宽扫 `only-here` **141 → 113** ✓（−28 = 7×4 ✓✓）；全量 **1105 passed** ✓。
- **当时仍差两处（已量 ✓）**：① **少的那 5 个** ✗ —— 它们是没有 Python 类的纯基线接口 ✓，当时拿不到迭代器位的发布 ✓ ⇒ **后来由"重设计 `array`"一节修好** ✓（形状进了数据 ✓）；② 两个重挂父的接口**上方那个原型** `constructor.name` 已是 `'Object'` ✓，但**不是页面里的 `Object.prototype`** ✓（Chrome 是 ✓）—— 与本条同源 ✓（实现/接口两张图还没完全分开 ✓），**仍未修** ✗。

### 补上**符号盲区**：整条链此前只比字符串键（新观测 ✓ + 修 14 处 ✓）

- **盲区**：`Object.getOwnPropertyNames` **从不列 symbol** ✗ ⇒ `Symbol.toStringTag`（`Object.prototype.toString.call(x)` 正是读它 ✓）与两个迭代 symbol **从未被两侧对照** ✗。两个表面探针现在都比四个带 ASCII 标签的符号 ✓（`@@iterator`/`@@asyncIterator`/`@@toStringTag`/`@@toPrimitive` ✓）—— 只标值得比的那四个 ✓：未标名的 symbol 会在两侧各自命名 ✗，读出来是"引擎的差异"其实是"名字的差异" ✗。
- **立刻抓到的族** ✓（`only-chrome` **51 → 74** ✓，全是符号 ✓）：`@@iterator` 缺 **14** 个 prototype ✓、`@@toStringTag` 缺 6 个（**就是那 6 个命名空间** ✓，与本条同源 ✓）、`@@asyncIterator` 缺 2 个 ✓、`location` 的 `@@toPrimitive` 缺 1 个 ✓。
- **已修** ✓：那 14 个都**没有 Python 类** ✗（`AudioParamMap`/`CustomStateSet`/`RTCStatsReport` 一类 Maplike/Setlike ✓）⇒ 四个具名方法由**基线成员**发布 ✓，但 `Symbol.iterator` 只能由 `array=` 位发布 ✗ ⇒ 无人发布 ✓。改法：`engine/binder.py` 在**没有手写 `array=`** 时，若基线在 prototype 上声明的 **`entries` 是 operation** ✓ ⇒ 只发 iterator 位 ✓（四个名字仍由基线给 ✓）。
- **踩到的坑（写下来 ✓）**：判据起初只看**名字** ✓ ⇒ `CSSMathMax` 的 **`values` 是 attribute**（长度 ✓，不是迭代器 ✓）被误判 ✗ ⇒ 一次多发 **13** 个 iterator ✗（`only-here` 113 → 126 ✓ 被扫出来 ✓）。收紧为"`entries` 且 `kind == operation`" ✓ ⇒ 多发降到 3 ✓。
- **判据**：`@@iterator` 的 `only-chrome` **14 → 0** ✓✓、`only-here` 的 `@@iterator` 13 → **3** ✓；总量 `only-chrome 74 → 60` ✓、`only-here 113 → 116` ✓ ⇒ **净减 11** ✓；全量 **1106 passed** ✓；守护 `test_a_maplike_interface_publishes_its_iterator_and_a_value_is_not_one` ✓ 两面都守（三个 Maplike 必须有 iterator ✓、`CSSMathMax` 必须没有 ✓ 而它的 `values` 属性仍要在 ✓）。
- **残留（已量 ✓）**：3 个多发 ✓（`FileSystemDirectoryHandle`/`Navigation` 一类 —— 在 Chrome 里**只异步可迭代** ✗，而 sync/async 这个区别**基线里没有** ✓ ⇒ 只能靠实测 ✓）；`@@asyncIterator` 2 个 ✓；`location[Symbol.toPrimitive]` 1 个 ✓。

### 为什么"照 `template.array` 发迭代位"这条路走不通（已量 ✗，写下来省下一轮 ✓）

- 与其等"少的 5 个"逐个修 ✓，先量了 `template.array=True` 的**全部 38 个**接口 ✓：Chrome 实际发布四个具名方法的有 **5 个** ✓（`CSSNumericArray`/`CSSTransformValue`/`CSSUnparsedValue`/`TimelineTriggerRangeList`/`XRInputSourceArray` ✓），另外 **30 个两侧都是 0** ✓（`FileList`/`MediaList`/`StyleSheetList`/`HTMLAllCollection` … ✓），还有 3 个本来就有 ✓（`NodeList`/`RadioNodeList`/`DOMTokenList` ✓）。
- ⇒ **同一个布尔值分不出这两类** ✗（`NodeList` 与 `HTMLCollection` 的 `template.array` 都是 `True` ✓）⇒ "见 `True` 就发五位" ✗ 会凭空多出 **120 个**多余成员 ✗。
- ⇒ 结论 ✓：这四个名字的真相**只能来自实测** ✓（profile ✓）—— 与 canvas 那族同一性质 ✓ ⇒ 当时写的下一步是"profile 增加一节：每个接口实测发布了哪四个" ✓。**后来分两步落成** ✓（见"重设计 `array`"一节 ✓）：先让**基线**承载词（抽取器读 IDL ✓），再让 **profile** 用同一套词记录实测（`array_words` ✓）—— 因为形状一旦是数据 ✓，引擎就不必在运行时猜 ✓。

### 重设计 `array`：从**布尔**到**词**，一个名字一个含义（已落地 ✓）

- **问题**：同一个 `array` 曾表示两件事 ✗ —— 基线里它是**布尔**（"这个绑定装了 `Array.prototype` 内建" ✓），`IdlSpec.array` 里它是**位掩码**（"借哪几个" ✓），而 `assembly.apply_template_flags` 正是把两者同名对接的那一行 ✗（把布尔翻译成 `"iterator"` ✓）。中间试过拆成 `array_proto` / `array_like` 两个名字 ✓，最终采用**保留单名、把取值扩成形状** ✓。
- **现在的语义**（一处定义 ✓）：`array` = "该接口借了哪些 `Array.prototype` 内建" ✓，取值 `"iterator"`（只借 `@@iterator` = `Array.prototype.values` ✓）/ `"iterable"`（再加 `entries`/`keys`/`values`/`forEach` ✓）/ **缺席**（不借 ✓）。JS 侧一眼可辨：

```js
typeof document.forms[Symbol.iterator]                    // "function"  -> array: "iterator"
typeof document.querySelectorAll("div")[Symbol.iterator]   // "function"  -> array: "iterable"
"forEach" in NodeList.prototype                            // true
typeof CSSMathMax.prototype[Symbol.iterator]               // "undefined" -> 缺席（它的 values 是 getter，不是迭代器）
```

- **四方对齐**：**抽取器**算出形状（生成的绑定里装了哪个 intrinsic ✓ + IDL 的 `iterable<>`/`maplike<>`/`setlike<>` ✓，排除 `async iterable<>` ✓；`_merge_template` 里 `"iterable"` 胜过 `"iterator"` ✓）→ **基线 `template.array`** 承载词（schema **5 → 6** ✓）→ **引擎只透传**（删掉那句布尔翻译 ✗、也删掉"按 `entries` 为 operation 派生"的规则 ✗ —— 该推导**上移到数据** ✓）→ **`IdlSpec.array` 只收词** ✓（位退回原生内部 ✓，`FlagArrayProto` 从公共 API 移除 ✓，改 `pyv8.ArrayProto.iterator/.iterable` ✓；读回也是词 ✓）。
- **实测校验这条规则**（908 个基线接口 ✓、Chrome 双侧读数 ✓）：只有三种形状 —— 无迭代器 839 ✓ / `iterator` 41 ✓ / `iterable` 21 ✓ ⇒ 两个词够用 ✓；**12 个接口**（`NodeList`、`DOMTokenList`、`CSSNumericArray`、`XRInputSourceArray` … ✓）的四个名字不在生成的表里 ✗（来自 IDL 的 `iterable<>`/`maplike<>` ✓）⇒ 抽取器必须读 IDL，否则误判 ✓。
- **迁移**（无 `CHROMIUM_ROOT` ✗）：`script/migrate_baseline_schema6.py`（`--check` 可空跑 ✓）重写三份 zip ✓（包内 1254 接口 + 仓库根两份版本副本 ✓）、删除派生的 `interface-baseline.json` 与 `.pyv8ccache` ✓、**并刷新 profile 的 `baseline_digest`** ✓ —— 不刷这一处，`_measured_against` 会静默失效、几百个 ghost member 会回来 ✗。
- **顺带把两条"待修"消掉了** ✓：少的 5 个（`CSSNumericArray` 一族 ✗）与三个**多发 iterator**（`FileSystemDirectoryHandle`/`Navigation` ✗）—— 它们原先被我判定需要"引擎派生"或"profile 收窄符号" ✗；形状一旦是数据，就没人需要在运行时猜 ✓。
- **profile 也存同一套词** ✓：采集器沿原型链找**自有 descriptor** 读 `@@iterator` 与那四个 ✓（**不读值** ✗ —— `CSSMathMax.prototype.values` 是 getter、`Cache.prototype.keys` 是异步方法 ✓），映射成 `array_words` 一节 ✓（本次 **62 个接口** ✓）；`InterfaceTable` **实测优先、其次基线** ✓；若某基线接口有 `@@iterator` 却只有**部分**具名方法，采集器**当场报错** ✓（词表只有两种 ✓，不让第三种静默降级 ✓；实测：基线接口里 0 例 ✓）。
- **判据**：`surface_wide` **40 / 113 / 11** ✓ 与迁移后**逐项一致** ✓、`iterable_probe` **20 行全部两侧一致** ✓（原先 5 行 `differs` ✓）；`_pyv8` 重建后 **28 passed** ✓、pyv8c 全量 **1106 passed** ✓；守护：`test_the_two_array_words_are_the_two_shapes_chrome_has` ✓、`test_array_takes_the_name_of_the_shape_a_caller_means` ✓（读回是词 ✓）、`test_the_array_word_is_the_shape_the_browser_publishes` ✓、`test_a_shape_no_word_carries_stops_the_run` ✓。

### 五个"环境事实"从写死改为采集（已修 ✓，第六个已量但未搬 ✗）

五处硬编码搬进 `foundation/environment.py` ✓（新增 `Field` + 采集探针 + 视图 ✓；页面经 `BrowserEnvironment` 读 ✓；原表降为"无 profile 时"的兜底 ✓）。重采 profile 量出**三处真差异** ✓：

| 键 | 写死的 | 真 Chrome | 影响 |
| --- | --- | --- | --- |
| `viewport.outer_inset_height` | 95 | **151** ✓ | `outerHeight` 一直差 56 ✗ |
| `speech.voices` | 3 个 `Microsoft …` | **19 个 `Google …`** ✓（`local_service: false` ✓） | `getVoices()` 报的其实是别人机器的声音 ✗ |
| `gpu.wgsl_features` | 13 项、顺序自定 | 13 项、**Chrome 的集合顺序** ✓ | `WGSLLanguageFeatures` 的迭代顺序 ✗ |

另两处实测与表一致 ✓（`media.constraints` 36 ✓、`permissions.*` 24/11/映射 ✓）⇒ 写进去只为把真相从代码挪进数据 ✓。

- **判据**：`surface_wide` 仍 **40 / 113 / 11** ✓（零回归 ✓）；`tests/browser/test_environment_answers.py` 逐键验证"覆盖环境 ⇒ 页面读数跟着变" ✓。
- **采集的坑**：`speech.voices` 必须等 `voiceschanged` ✓（Chrome 异步填 ✓，直接读恒为空 ✗）；`permissions.query` 对 `push` 要带 `userVisibleOnly: true` ✓，否则该名字被拒 ✗。
- **第六个（`performance.entry_types`）未搬 ✗，理由是实测的**：它是 **interface 上的静态属性** ✓（`assembly.py:245` 的 realm 注入只给**静态方法** ✓）⇒ 读它时**没有 realm** ✗。试过的两条捷径都不成立 ✗：① 在该 getter 里调 `Window._accessor_window()` 取当前窗口 —— **实测会让后面的 frames 导航用例开始偶发失败** ✓（`window.name` 跨导航丢失 ✗，单独跑 3/3 过 ✓、加上就 1/3 败 ✓），已撤回 ✓；② 改用实例路径 ✗ —— 属性是静态的 ✓，实例上看到的与页面上读到的必须是同一个值 ✓。**判据**：引擎支持给 interface 属性注入 realm 后 ✓，`PerformanceObserver.supportedEntryTypes` 才能在覆盖环境时跟着变 ✓。
- **测试本身的坑（写下来 ✓）**：新用例里那个 2 秒的"泵任务队列"循环 ✗ 会让**之后**跑的 frames 导航用例偶发失败 ✓（`tests/browser/test_surface_members.py` 也有同样的泵 ✓，但它排在 frames 用例**之后** ✓ 所以一直没暴露 ✗）⇒ 现在该文件**只做同步断言** ✓，异步那两项（promise / 声音 ✓）改成不用浏览器的单测 ✓。
- **顺带发现（未修 ✗）**：`WGSLLanguageFeatures` 这类 setlike 借 `array="iterable"` 的 `values` ✓，而该内建按 `length` 取元素、setlike 没有 `length` ✗ ⇒ `[...wgslLanguageFeatures]` 采到 **0** 项 ✗（`size`/索引正常 ✓）。真 Chrome 展得开 ✓ ⇒ 需给这类集合自备 `@@iterator` ✓，另立一条 ✓。

### 接口原型的 `constructor` 缺失 ⇒ 页面读到的是 `Object`（已修 ✓）

- **症状**（两侧读数 ✓）：10 个接口的原型上 `constructor` 是 `fn:1` ✗，Chrome 是 `fn:0` ✓ —— `BeforeUnloadEvent`/`CSSRule`/`CSSGroupingRule`/`CSSStyleRule`/`CSSStyleSheet`/`StyleSheet`/`RTCRtpReceiver`/`CanvasRenderingContext2D` ✓。
- **逐层复现**（决定性 ✓）：`CSSStyleSheet.prototype` 与 `StyleSheet.prototype` 都**没有自有 `constructor`** ✗ ⇒ 链落到 `Object.prototype.constructor` ✓（`Object.length` 正是 **1** ✓）⇒ 那 10 个 `fn:1` 的**真身就是 `Object`** ✗。
- **根因**：这些类的 spec 写着 `has_constructor=0` ✗，而引擎在"类写了 spec"时就照它办 ✗（`engine/binder.py` ✓）—— 与 `engine/features.py:20` 那句"realm exposure 归 Chromium 基线、类不再声明"直接矛盾 ✓。
- **改法**：曝露一律取基线的 `has_interface_object` ✓；"不可构造"交给 `constructor_behavior` ✓（本来就是基线说了算 ✓ `assembly.py:517` ✓）。
- **顺带定下一条规则** ✓：接口对象没有构造器 ⇒ `length` 为 0 ✓ —— 参照读数 **548/548**、零例外 ✓ ⇒ 同时修掉 `SnapEvent`/`FencedFrameConfig` 的 arity ✗。
- **判据**：宽扫 `kind` **11 → 2** ✓、`only-chrome 40` / `only-here 113` 未变 ✓（零回归 ✓）；新增守护 `tests/browser/test_collections.py::test_a_non_constructible_interface_object_is_its_prototypes_constructor` ✓（9 个接口：自有 ✓、同一对象 ✓、arity 0 ✓）；`tests/repros/corpus.py` 那条的 `known_divergence` 已**删除** ✓（`test_repros.py` 的规矩正是"不再分歧就删" ✓）；全量 **1118 passed** ✓。
- **仍未修（都带判据 ✓）**：① `SnapEvent`/`FencedFrameConfig` 的 arity ✗（基线 `allow_new` 与 arity 都记错 ✓，真身是"IDL 只数**必填**参数" ✓ ⇒ 抽取器/数据 ✓）；② `NodeFilter` 有 `prototype` ✗（Chrome 没有 ✓，与命名空间同一个原生限制 ✓，判据 `'prototype' in NodeFilter === false` ✓）；③ 接口对象的 `__proto__` 没接 `Function.prototype` ✗（`Audio`/`Image`/`Option` 的 18–27 项 only-here ✓，判据 `Object.getPrototypeOf(window.Audio) === Function.prototype` ✓ —— 与本仓"实现/接口两张图未分开"同源 ✓）。

### 重构：`__v8_idl_spec__` 从"清单"退成"差异"（已落地 ✓）

- **引擎按约定解析**（`engine/binder.py` ✓）：每个基线成员依次问 **宿主 override → 类自己的差异声明 → `fn_<name>`/`get_<name>`+`set_<name>` 约定 → 桩** ✓；位置、arity、property flags、`side_effect`、cross-origin **一律取基线** ✓，attribute 可写性取基线 `setter_state`，基线不表态时才由声明/约定决定 ✓。
- **实测三件事**（都是先量后改 ✓）：
  1. 今天"有约定名方法、spec 却没声明"的成员 **0 个** ✓ ⇒ 约定这一层是纯下沉 ✓，落地即行为中性 ✓；
  2. 声明里的 `location`/`property_flags`/`side_effect`/`cross-origin` **引擎从不读** ✓（实测 `AbortController.signal` 声明 `side_effect=1`、编译结果 = 基线的 `0` ✓）⇒ 1382 条 `flags` 声明与重述同类 ✓；这类"我们曾声明、绑定从不读"的记录存档在 `spec-member-claims.json` ✓；
  3. 声明**基类也声明同一成员**时不能单独删 ✓ —— 删掉子类声明后引擎会回落到父类那条（没写 setter ✗），writer 变成桩 ✗。
- **规模**：`declared` **2406 → 197** ✓、`restating` **815 → 31** ✓、`flags` **1382 → 6** ✓、带 spec 的类 **366 → 95** ✓；共删约 **2900 行** ✓（成员条目 ✓ + 接口级字段 955 处 ✓ + 惰性 flags 1079 行 ✓ + 推导式字段 ✓ + 空 `IdlSpec()` 271 处 ✓）。残留 31 条记在守护测试注释里 ✓（它们同时充当**实现来源**：`ValidityState` 的校验位、`PerformanceTiming` 的时间点 ✓，拿掉名字会连实现一起拿掉 ✗）。
- **空 `IdlSpec()` 不是"标记"而是噪声** ✓：引擎读的是 `cls.__dict__.get("__v8_idl_spec__", IdlSpec())` ✓ ⇒ 空的那个与"没有"同义 ✓（267 处 ✓ 已删 ✓，判据同样是编译面零差异 ✓）。唯二的例外是**锚点**：`X.__v8_idl_spec__ = X.__v8_idl_spec__.replace(...)` 要读它 ✓，而且类体没了 spec 后这行会**静默读到父类的 spec** ✗ ⇒ 这类引用改写成 `IdlSpec().replace(...)` ✓（`EventTarget` ✓、`HTMLElement` ✓ 两处 ✓）。
- **中性判据**：`script/dump_compiled.py` 前后快照 ✓ —— 1254 接口 / 10361 成员的 `methods`/`attributes`/`handlers`/`fields` **逐项零差异** ✓（唯一两处差异是**修好的 bug** ✓）：`Element.setAttributeNS` 有实现、spec 漏声明 ⇒ 旧行为下是**桩** ✓（调用静默无效 ✗）；`CharacterData.data` 声明只写 getter ⇒ 旧行为下 setter 是**桩** ✓（Chrome 里 `data` 可写 ✓）。
- **工具留档**：`pyv8c/tools/idl_audit.py`（谁在重述 ✓，分 `restating`/`flags`/`renamed`/`resolved`/`unresolved` ✓）、`script/dump_compiled.py`（编译面快照 ✓）、`script/trim_spec_restatements.py`（按审计删行 ✓，含 `--check`/`--fields`/`--generators`/`--fix`/`--tidy` ✓）。守护测试：`tests/unit/test_spec_is_a_difference.py` ✓、`tests/browser/test_convention_binding.py` ✓、`tests/unit/test_compile_cost.py` ✓。
- **性能**：`tools/measure_startup.py` 实测 —— 已编译 spec 进程级复用 ✓（第二个 `Bindings` **0 次未命中 / 编译 8.5 ms ✓**）；首屏余下 288 ms 是 materialize（本步未动 ✓）。缓存只在**可互换**的 bindings 间共享 ✓（同基线 ✓、同 profile ✓、无宿主钩子 ✓、静默桩 ✓、且本次编译没报不匹配 ✓ —— 最后一条保证 `baseline_mismatch="warn"` 仍按 bindings 报一次 ✓）。

### 帧文档没有自己的地址时，相对 URL 现在按**创建者**解析（已修 ✓）

- **症状**（真站 2026-10-10 ✓）：

```
WARNING pyv8c.platform.lifecycle:dispatch_script_error - a script's failure was the host's own:
: is not a scheme a page loads, so nothing was requested </cdn-cgi/challenge-platform/scripts/jsd/main.js>
```

  冒号前**没有 scheme** ✓ ⇒ 那个根相对 `src` 根本没被解析 ✓ ⇒ 挑战自己的 jsd 脚本一个字节都没请求 ✓。
- **两侧证据**（`script/base_url_frames.py` ✓，本地 fixture、零第三方流量 ✓，同一页里三种形状各用一个不同路径 ✓；
  帧自己把 `document.baseURI`、`String(s.src)`、`load`/`error` 事件 `postMessage` 出来 ✓，父页回读 C 的文档 ✓）：

  | 形状 | Chrome | pyv8c | 判定 |
  | --- | --- | --- | --- |
  | B 普通 `srcdoc` 帧 | 请求 `https://probe.test/B.js` ✓、`__b_ran = 1` ✓ | 修前是 `/B.js`（无 scheme ✗）⇒ 修后请求同一条 ✓、脚本也跑了 ✓ | **已对齐 ✓** |
  | A `srcdoc` + `sandbox="allow-scripts"` | `baseURI` 是父页 URL ✓、`s.src` 解析成 `https://probe.test/A.js` ✓、随后 **`error` 事件且不发请求** ✗ | 解析成同一条 ✓、**照发并成功加载**（`load` 事件 ✓） | **未对齐 ✗** |
  | C `about:blank` 帧（父页写） | `contentDocument` 同步可用 ✓、`scripts:1 / ran:'1'` ✓ | `contentDocument` 是 **null** ✗（`TypeError: Cannot read properties of null (reading 'write')` ✓）、`scripts:0` ✗ | **未对齐 ✗**（根因已定 ✓） |

- **撤回一条**（上一轮写的"Chrome 对 sandbox `srcdoc` 直接丢弃"✗）：那是**探针自己**造成的 ✓ ——
  A 的 srcdoc 里首个内联脚本执行时，Chrome 的 `document.body` 还是 **null** ✗ ⇒ `appendChild` 抛错 ⇒
  既不发请求也不触发事件 ✓（正是 skill 坑表第一条"测量工具自己干扰被测对象"✓）。给那段 srcdoc 补上
  `<body>` 之后，Chrome 的行为变成"解析成功 + `error` 事件" ✓（读数见上表 ✓）。
- **A 的未对齐点与下一步** ✗：Chrome 解析成功却**拒发**这次加载 ✓（有 `error`、`page.on("request")` 无记录 ✓），
  我们照发并成功 ✓ ⇒ 差在"opaque origin 的帧到底能不能取这个子资源" ✓。这条**还需要更强的见证** ✗：
  现在的见证是 Playwright 侧的请求表 ✓，而 Chrome 可能在它之前就拒了 ✓ ⇒ 下一步改用**本机小服务**当见证
  （服务端计数 ✓、不经拦截 ✓）再判一次 ✓。
- **C 的根因（在代码里对上了 ✓）**：`HTMLIFrameElement.get_contentDocument()` 在 `_child_services is None`
  时返回 `null` ✓，而子上下文由 `platform/resource_loading.py` 的 `ensure_iframe_context()` 建 ✓ ——
  它的 docstring 自己写着"**插帧的那个任务之后**建 ✓，因为**在一个运行中的脚本里建 realm 不可重入**" ✗。
  Chrome 是**创建即有初始 `about:blank` 文档** ✓ ⇒ 父页可以立刻写进去（挑战正是用这种写法注入 jsd 的 ✓）。
  **改法形状**（与本案已修的"帧提交就地执行"同一族 ✓）：让这两个 getter 在被访问时**就地**建这个初始上下文
  （没人访问就不付代价 ✓），并只走"无 `src`/`srcdoc` 的初始文档"这条 ✓。**判据**：C 行两侧同集合 ✓
  （`contentDocument` 非 null ✓、`/C.js` 被请求 ✓、脚本把 `__c_ran` 置 1 ✓）。

- **根因**：`about:blank` / `about:srcdoc` 的文档自己的 URL 没有 scheme ✓，而资源解析拿的是
  `page.url` ✗ ⇒ `urljoin("about:srcdoc", "/cdn-cgi/…")` 得到一条**路径** ✗ ⇒ 随后被
  `foundation/urls.py` 的 `unloadable_scheme()` 判成"不是页面能加载的 scheme" ✗（就是那条 WARNING ✓）。
- **改法**：`web/bom/utils.py` 新增 `page_base_url(page, url=None)` ✓ —— 沿 `parent_page` 走到第一个
  有 scheme 的地址 ✓，与同文件里 `page_secure_context()`（2026-10-06 修的那条 ✓）**同一套继承规则** ✓；
  接在 `platform/resource_loading.py`（同步/异步加载 ✓ + 两处 iframe `src` ✓）与 `platform/navigation.py`
  （两处导航 ✓）上 ✓。文档自身的 URL 不动 ✓：`location.href` 仍读 `about:blank` ✓。
- **判据**：`script/base_url_frames.py` 的 B 行两侧逐字一致 ✓（修前是 `DIFFERENT` ✗）；守护
  `tests/browser/test_frame_base_url.py` 三条 ✓（普通 srcdoc ✓ / sandbox ✓ / 自有地址的文档仍按自己解析 ✓）；
  全量 `tests`（除 repros）**1241 passed, 1 skipped** ✓（唯一 error 是既有 devtools 抖动 ✓，隔离跑 2/2 通过 ✓）。

### 挑战的热循环在读全局名：我们是 Python 访问器，Chrome 是数据属性（2026-10-10 两侧已量 ✓，未修 ✗）

- **症状**（`script/5s.py --trace`，90 s 窗口 ✓）：`[FRAME] getter: Window.Reflect` **13 万次** ✓、
  `get:isNaN` **68 万次** ✓、`get:String` **68 万次** ✓（三个名字的次数几乎相等 ⇒ 同一个循环体每次各读一次 ✓）；
  realm 整窗不 idle ⇒ 判定脚本收尾时 `page.evaluate` 也被 `RealmBusyError` 挡在 60 s 上 ✗。
- **不是页面自己死循环** ✗：`script/trace_reflect.py` 把每次读归到"正在执行的脚本 + realm"✓
  （包 `ScriptController.evaluate` + thread-local ✓；trace 回调与脚本同线程 ✓），并把栈打出来 ✓。
  第 **20 万次**读的栈是 `xhr.py:97 _state → events._invoke → _js_call(handler, …)` ✓
  ⇒ 读发生在**页面某个 XHR `readystatechange` 处理器的一次调用里** ✓（只有最早的读落在
  `scripting.py:72 exec_js` ✓）。带 trace 时这一次调用是分钟级 ✗；**不带 trace 时
  `script/slow_handlers.py` 量到 244 个回调里没有一个超过 0.8 s** ✓ ⇒ 那 67 万次读的绝大部分
  开销来自**日志本身** ✗（`--trace` 把它放大成分钟级 ✓）。
- **两侧代价**（本地 fixture ✓、各 5 万次 ✓、无 trace ✓）：

  | 形态 | pyv8c | Chrome | 倍率 |
  | --- | --- | --- | --- |
  | `window['Math']` 读 | 56.5 ms | 0.4 ms | **~140×** ✗ |
  | `performance.now()` ×2 | 178.7 ms | 13.1 ms | ~13× ✗ |
  | `Reflect.get(o,'a')` | 1.0 ms | 1.0 ms | 1× ✓ |

  命令：`python script\reflect_cost.py`（`_ask_page` 的两侧探针 ✓）—— `Reflect.get` 同速 ✓、
  只有**读全局名**慢 ✗ ⇒ 不是"JS 普遍慢" ✗。
- **根因**：这些名字由 binding 装成**访问器** ✓（trace 把每一次读都记成 `getter: Window.X` ✓
  就是见证 ✓），所以页面每读一次全局名都要绕一趟 Python ✗（~1.1 µs ✓）；Chrome 把同样的名字
  放在全局对象上做**数据属性** ✓（V8 可以把它提到循环外 ⇒ 8 ns ✓）。`Math` 那一行的两侧差
  就是这条机制的直接读数 ✓。
- **判据**：修好后 `window['Math']` 读应进入 ns 量级 ✓，且 `script/5s.py --trace` 不再出现六位数级的
  `get:String`/`get:isNaN`/`get:Reflect` ✓。
- **未修的原因** ✗：全局名的形状由 `engine/binder.py` 决定（成员按需物化 ✓），改成数据属性要动
  "谁能当数据属性、何时冻结"那条边界 ✓ ⇒ 留给下一轮；本轮只把读数与判据钉住 ✓。

### 收尾停在 `fo@cf` 之后：本轮交换表（2026-10-10 ✓，`--trace` 与无 trace 各一轮 ✓）

```
GET  https://steamdb.info/                              -> 403
GET  …/orchestrate/chl_page/v1                          -> 200 247521B
GET  challenges.cloudflare.com/turnstile/v0/…/api.js    -> 200  86988B
POST steamdb.info/…/fo/…                                 -> 200 107099B
GET  …/turnstile/f/…                                     -> 200 268399B
POST challenges.cloudflare.com/…/fo/… (785795B)         -> 200   ← 之后不再有请求 ✗
cookies (0)  ->  VERDICT: FAIL
```

- 比文档上一轮（2026-10-07 ✓）**更早**：那时 `pat`/`ci` 已出现 ✓，本轮没有 ✗ —— 挑战每轮变体
  不同 ✓，只能比集合、不能比条数 ✓（skill 的读法 ✓）。
- **修掉"帧文档 base URL"之后重跑（同一 60 s 窗口 ✓）**：那条 WARNING 不再出现 ✓、
  `GET https://steamdb.info/cdn-cgi/challenge-platform/scripts/jsd/main.js` 真的发出去了 ✓，
  交换表**回到了 `pat` 401 ✓ / `ci` 200（18820B）✓** ⇒ "停在 `fo@cf` 之后"确实是被那条被丢掉的
  脚本卡住的 ✓（机制与判据见"帧文档没有自己的地址时"一节 ✓）。
- 停住的真因（本轮已修 ✓）：挑战自己的 jsd 脚本因为**帧文档 base URL** 被丢掉 ✓（见上一节 ✓）——
  修好后同一窗口就走到了 `pat`/`ci` ✓。判定脚本仍会收在 `killed after 200s` ✓（realm 忙 ⇒
  `page.evaluate(SNAPSHOT)` 读不到页面状态 ✓），那是**判读脚本**的局限 ✗，不是挑战停住的因 ✗
  （上一段"realm 整窗忙"的读数只在带 trace 时成立 ✓，别当成停住的解释 ✗）。

### 缺的成员：canvas/WebGL/ImageData 一族与 `TextDecoder.decode(ArrayBuffer)`（Chrome 侧已实测 ✓，pyv8c 侧待复现 ✗）

`trace_analyze.py` 的"答 undefined / 抛错"里，除已撤回的三条 ✓ 之外还有四条：

| 读 / 调用 | pyv8c | Chrome（实测 ✓） |
| --- | --- | --- |
| `OffscreenCanvasRenderingContext2D.getContextAttributes()` | `undefined` ✗（7 次 ✓） | `{"alpha":true,"colorSpace":"srgb","colorType":"unorm8","desynchronized":false,…}` ✓ |
| `WebGL2RenderingContext.getInternalformatParameter(RENDERBUFFER, RGBA8, SAMPLES)` | `undefined` ✗（34 次 ✓） | `object:4`（`Int32Array` ✓） |
| `ImageData.pixelFormat` | `undefined` ✗（4 次 ✓） | `"rgba-unorm8"` ✓ |
| `TextDecoder.decode(<ArrayBuffer>)` | `TypeError` ✗（2 次 ✓） | `"ABC"` ✓ |

- **Chrome 一列来自实测** ✓（`script\trace_gaps.py` 的两侧探针 ✓：同一页在 Chrome 里
  `decode ArrayBuffer → ABC` ✓、`imagedata pixelFormat → true:string:rgba-unorm8` ✓、
  `webgl2 internalformat → object:4` ✓、`canvas attrs` 有对象 ✓）。
- **pyv8c 一列只有真站 trace** ✗：同一份 fixture 页在 pyv8c 侧 `window.__out` 是 `undefined`
  （页内脚本没执行 ✓，而 Chrome 侧同页正常 ✓）⇒ **"本地两侧复现"这一环本轮没做通** ✗。
  下一步的第一件事是查清"fixture 页在 pyv8c 里为什么不执行脚本" ✓（`script\reflect_cost.py`
  的 fixture 是执行的 ✓，差别只在页面内容 ✓）。
- 与既有条目同源 ✓：canvas 那族的根因是**基线快照缺 mixin** ✓（见"未解决"表 ✓），
  `getContextAttributes`/`getInternalformatParameter`/`pixelFormat` 很可能同因 ✓。

## 撤回（被实测推翻的推断）

| 曾推断 | 实测结论 |
| --- | --- |
| "cookie 罐空 ⇒ 我们没收 `Set-Cookie`" ✗ | **先撤回、后修正**：25–30 s 窗口内服务器确实没发 ✓；**90 s 窗口里发了 `cf_clearance`**，而罐里只留下 `cf_chl_rc_ni` ✗ ⇒ 真问题在"存不住" |
| "`pat/<ray>` 回 401 ⇒ 我们被拒" ✗ | **撤回**：Chrome 同样 401（只是响应体 1 B vs 我们 4 B） |
| "`ci/<ray>` 从未发出 ⇒ widget 不请求它" ✗ | **撤回**：另一轮里 `ci/<ray>` 出现了（200 / 32 KB PNG）⇒ 挑战每轮变体不同 ✓，只能比较**集合**，不能比较条数或顺序 |
| "trace 里 `Navigator.cpuPerformance` / `Window.event` / `Window.documentPictureInPicture` 读到 `undefined` ⇒ 我们缺实现" ✗ | **撤回**：两侧表面比对里这三个都**不在** `only-chrome` ✓ ⇒ Chrome 同样读 `undefined` ✓（`cpuPerformance` 不是标准成员 ✓、`window.event` 只在事件派发中才有值 ✓、`documentPictureInPicture` 在参考 Chrome 里也没开 ✓）。教训：trace 只说明"我们读到 undefined" ✓，**必须**再问 Chrome 读到了什么 ✓ |

## 未解决 / 下一步

| 差异 | 状态 / 证据 |
| --- | --- |
| **迭代器方法装错接口** ✗ → **两侧已全同** ✓ | **多**的一半：7 个接口（`HTMLCollection` 家族 3 + `NamedNodeMap` + `Plugin`/`PluginArray`/`MimeTypeArray` ✓）各多 4 个 ✗ —— 已修 ✓（"迭代表面"一节 ✓）。**少**的一半：`CSSNumericArray`/`CSSTransformValue`/`CSSUnparsedValue`/`TimelineTriggerRangeList`/`XRInputSourceArray` 各少 4 个 ✗ —— 它们是没有 Python 类的纯基线接口 ✓，**由"重设计 `array`"一节修好** ✓（形状进了基线数据 ✓）。**判据**：`iterable_probe.py` **20 行全部两侧一致** ✓（原先 5 行 `differs` ✗）；宽扫 `only-here 116 → 113` ✓、`only-chrome 60 → 40` ✓ |
| 零星的 interface 面差异 ✗ | `console.memory` / `console.createTask` ✓、`WebAssembly.compileStreaming` / `instantiateStreaming` ✓、`Iterator.prototype.includes`/`join` 与 `Iterator.zip`/`zipKeyed` ✓、`Date.prototype.toTemporalInstant` ✓、`window.Audio` 上多出 `Object.prototype` 那一串（`hasOwnProperty`/`valueOf`/… ✓）。全部来自 `--probe surface_wide` 的聚合 ✓，逐条明细见 `surface-wide-report.txt` 与 `surface-surface_wide-*.json` ✓ |
| **`canvas.getContext('2d')` 少 8 个属性**（根因已定 ✗，未修） | `globalAlpha`/`imageSmoothingEnabled`/`lineDashOffset`/`lineWidth`/`miterLimit`/`shadowBlur`/`shadowOffsetX`/`shadowOffsetY` ✓：Chrome 有、我们读到 `undefined` ✗（`surface_diff.py` 的 `canvas.2d` 行 84 vs 76 ✓）。**根因不在代码** ✓：`Idl.load().member('CanvasRenderingContext2D','lineWidth','attribute') -> None` ✓ 而 `fillStyle -> Member(...)` ✓ ⇒ **基线快照**（`baselines/idl.json` ✓ 由 `tools/extract_idl.py` 从 Blink bindings 抽取 ✓）缺这一族 mixin ✓。就地声明会被引擎自己的规矩挡住 ✓（`Bindings.validate()` 要求声明不得与基线矛盾 ✓，实测就是这条拦住 ✓）。**判据**：用 `CHROMIUM_ROOT` 重新生成基线后，`canvas.2d` 的 `only-chrome` 8 → 0 ✓ 且 `pytest tests/unit/test_baseline_discrepancies.py` 仍绿 ✓ |
| `Response.json`（静态）缺失 ✗ | Chrome：`typeof Response.json === 'function'`、`length` 1 ✓；我们 `undefined` ✗。**根因**：成员表以**名字**为键 ✓（`engine/binder.py:_members` ✓）⇒ 一个接口无法在**两处**声明同名成员 ✓，编译器直接拒绝（实测 `TypeError: Response declares duplicate methods: json` ✓）。已**撤回**这次尝试 ✓ 并把原因写在 `response.py` 的 spec 注释里 ✓。**判据**：支持 (name, location) 双键后，`Response.json` 出现且**实例** `response.json()` 仍在 ✓（守护测试现在就盯着实例那一个 ✓） |
| `window.Temporal` 缺失 ✗ | Chrome 有 `Temporal` 命名空间 ✓（`value:object !enum` ✓），我们 `undefined` ✗。这是一整块实现（日历/时区 ✓），未修 ✓ |
| **6 个命名空间被做成函数** ✗（`CSS` + 五个 `GPU*`） | Chrome：`value:object` ✓ 且**没有** `length`/`name`/`prototype` ✓；我们：`fn:0` ✗ 且带 `Function.prototype` 那一套 ✓（`only-here` 48 项 ✓ = 6×8 ✓；原先 50 ✓ —— 多的那 2 项是下面那条，已修 ✓）。**基线已经说了是哪六个** ✓：`constructor.allow_call is None` 且 `allow_new` 为 false ✓（= 生成的绑定里根本没有构造器回调 ✓；IDL 那条来源把同一件事写作 `False` ✓）——**6/6 命中** ✓、反向 **0/909** ✓（`script/baseline_join.py` ✓）。**为什么没修** ✗：接口对象由**原生绑定**创建 ✓，`definition_kind` 只标 Python 类模板 ✓（`_pyv8/src/interfaces/pyclass.cpp` ✓），`has_constructor=0` 也不把全局上的那个变成对象 ✓ —— 两种都实测过 ✓（`typeof CSS` 仍是 `'function'` ✓）⇒ 需在原生层或按 `chrome.js` 那样的 JS bootstrap 做 ✓（撤回的推导见 `engine/binder.py` 里的判据注释 ✓）。**判据**：`typeof CSS === 'object'` 且 `Object.getOwnPropertyNames(CSS)` 不含 `length`/`name`/`prototype` ✓ |
| `FencedFrameConfig`/`SnapEvent`/`canvas.2d.constructor` 的 `length` 差 1 ✗ | 我们 `fn:1` vs Chrome `fn:0` ✓（`Function.length` 是常见指纹位 ✓）。来源是基线里的构造器 arity ✓，随基线一起重生成 ✓ |
| **收尾仍差最后一步：`POST steamdb.info/…/fo/` 从未发出**（当前焦点 ✗） | 2026-10-07 两轮都能走到 `… → ci → fo@cf` ✓（Chrome 是 `… → ci → fo@cf → fo@steamdb` ✓），差的正是这次"回投站点"——`cf_clearance` 挂在它的响应上 ✓。widget 自报 `验证成功。正在等待 steamdb.info 响应` ✓ 且面板是 `cf-error` ✓。**判据**：出现 `POST steamdb.info/…/fo/`，且它的响应带 `set-cookie: cf_clearance` |
| `i@hagen`（只有我们）+ 它的 `cf-chl-peek` 回包 | trace：同一个 worker 被要求 `try{fetch("https://<随机子域>.challenges.cloudflare.com/…/i/…")}` ✓ ⇒ 我们**多探了一步** ✗；Chrome 全程不请求它 ✓。**判据**：`i` 消失（或证明 Chrome 也会发） |
| `pat` 时好时坏：401 ✓ / 400 ✗ | 一轮 401（与 Chrome 同 ✓）、一轮 400 ✗；400 那轮的响应**带着 `cf-chl-out`** ✓ 但体为 0 ✓，且顺序也不同（`i` 在 `pat` 前/后 ✓）⇒ 疑似与"偷看"那一步有关 ✓。**判据**：固定 401 |
| `cf-chl-out` 的**消费**仍不完整 | 修好 `Content-Type` 后它出现了 ✓（响应头见证 ✓），但 widget 没据此走完 ✗ ⇒ 下一步比对两侧 `cf-chl-out` 的**值与结构**（Chrome 那两条 ✓） |
| `cf_clearance` 被下发却没留在罐里 | 已被上面两处拦在更前面（现在根本走不到下发那一步 ✗）；见证仍就位 ✓（`JAR_EVENTS` ✓、`jar=[before -> after]` ✓）⇒ 走到那一步时立刻定论 |
| 首个 `GET https://steamdb.info/` 少一次 **307** | Chrome 是 `307 → 403` ✓，我们直接 403 ✗。**判据**：两次的 `Location` 与随后 cookie 行为一致 |
| `fo@steamdb` 数量 1 vs 2 | Chrome 收尾会再发一次 ✓ —— 与"最后一步"同因 ✓ |
| 旧文档的任务在 commit 后仍在跑 | 症状已挡住 ✓（`foundation/promises.py` 的关闭兜底 ✓）；根治仍是"别再跑旧文档的任务" ✓（`navigation.py:59` 只取消定时器 ✗）。**判据**：替换文档后旧任务不再产生 trace |
| OPFS `not implemented`（2 条 ✓） | 诚实桩 ✓（`navigator.storage.getDirectory()` ✓），站点会读成"环境不合格" ✓。**判据**：按 Chrome 的失败语义返回（`NotSupportedError` ✓）或实现 |
| **全局名是 Python 访问器 ⇒ 挑战的热循环慢 ~140×**（本轮焦点 ✗） | 两侧已量 ✓：`window['Math']` 5 万次 56.5 ms vs Chrome 0.4 ms ✓、`performance.now()` 178.7 vs 13.1 ✓、而 `Reflect.get` 同速 ✓ ⇒ 只有"读全局名"慢 ✓。读者是页面 XHR `readystatechange` 处理器的一次调用 ✓（栈证据 ✓，`script\trace_reflect.py` ✓）；带 trace 是分钟级 ✗、不带 trace 无 0.8 s 以上回调 ✓ ⇒ 大头是日志放大 ✓。**判据**：`window['Math']` 进入 ns 量级且 trace 不再出现六位数 `get:String`/`get:isNaN`/`get:Reflect` ✓ |
| `getContextAttributes` / `getInternalformatParameter` / `ImageData.pixelFormat` / `TextDecoder.decode(ArrayBuffer)` 缺 ✗ | Chrome 侧已实测 ✓（`script\trace_gaps.py` ✓：对象 ✓ / `Int32Array(4)` ✓ / `"rgba-unorm8"` ✓ / `"ABC"` ✓）；pyv8c 侧只有真站 trace ✓（同一 fixture 页在 pyv8c 里不执行脚本 ✗ ⇒ 本地复现待做 ✓）。**判据**：同一 fixture 页两侧逐字一致 ✓ |
| `script/5s.py` 收尾的 `page.evaluate(SNAPSHOT)` 在 realm 忙时挡 60 s ✗ | 交换表与罐仍能打出 ✓，判定行只能等 200 s 硬杀 ✓。**判据**：收尾有界（realm 忙时跳过快照并照打 FAIL ✓） |
| 帧文档 base URL：**A（sandbox `srcdoc`）与 C（`about:blank`+父页写）两形仍不同** ✗ | 两侧读数见 `script/base_url_frames.py` ✓（三种形状同页 ✓，帧自报 `baseURI`/`s.src`/事件 ✓）：A —— Chrome 解析成 `https://probe.test/A.js` ✓ 却**拒发**（`error`、无请求 ✓），我们照发并成功 ✓；C —— Chrome 的 `contentDocument` 同步可用且脚本跑了 ✓，我们的 `contentDocument` 是 **null** ✗（`ensure_iframe_context()` 把初始文档放到"插帧后的任务"✓，docstring 明说"运行中的脚本里建 realm 不可重入"✗）。**判据**：A 用本机小服务当见证再判一次 ✓；C 的 getter 就地建初始文档后两侧同集合 ✓ |

**两个观测口径的修正** ✓（都已落进脚本 ✓）：
- Playwright 的 `request.headers` 只是**子集** ✗（导航请求竟只列出 3 个头、连 `accept-language` 都没有 ✓）⇒ Chrome 参照必须用 `all_headers()` ✓，否则比头会造出**假差异** ✗；
- PowerShell 的 `>` 重定向默认写 **UTF-16** ✗ ⇒ 按 UTF-8 读会一个字符都匹配不上 ✓ ⇒ `flow_compare.py` 改为**按 BOM 自判编码** ✓（工具先"大声说出它看到了什么" ✓）。

## 方法与坑（可复用到别的案子）

- **每次重新分析都重跑一份新 trace**：`script/5s.py --trace --seconds 90` ✓（窗口 90 秒见上；挑战每轮变体不同，旧日志会把上一轮的结论带偏 ✗）。
- **`script/trace_sweep.py` 是入口**：四个桶（引擎抛错 / dispatch error / not implemented / 页面未捕获）+ `--errors`（异常连同其后几行 ✓）+ `--before eb/`（上报请求之前发生了什么 ✓）。
- **只读插桩是定位 `AttributeError` 这类"引擎自伤"的最快路径**：trace 只给异常类型 ✗，把可疑回调包一层打印 `traceback` ✓（`script/5s.py` 的 `watch_calls()` ✓）一次就能拿到文件与行号 ✓。
- **表面类差异用 `script/surface_diff.py`**：两侧同一探针枚举成员 ✓，三个计数器（`only-chrome` / `only-here` / `kind` ✓）就是判据 ✓；`--only-missing` 只看"页面读得到、我们读 `undefined`"的那一类 ✓；全量名单落在 `surface-surface-pyv8c.json` / `surface-surface-chrome.json` ✓（探针名在前 ✓）。
- **差异找到之后先问"基线知不知道"** ✓：`script/baseline_join.py` 把两侧读数与包内 IDL（`Idl.load(profile=None)`）按名字 join ✓，输出 `allow_call/allow_new/length/definition_kind/parent` ✓（该列原为 `as_object` ✓ —— 形状标志收敛成一个词后随之改名 ✓）—— 这一个问题就决定了改哪儿：**基线有**（引擎忽略了给它的信息 ✓）就改引擎 ✓，**基线没有/更宽**（✗）就是数据 ✓，需要重生成或收窄 ✓（canvas 那族与 `CSS` 那两条是后者 ✓）。`--holder window --chrome value --all --interfaces` 就是"基线里哪些接口 Chrome 报成对象" ✓，反向 `--chrome fn` 是防假阳性的那一步 ✓。
- **引擎日志走 loguru，不是标准库 logging** ✗：两侧运行器里 `logging.getLogger("pyv8c").setLevel(...)` **什么都不做** ✓ —— 正确做法是 `loguru.logger.disable("pyv8c")` ✓（`skills/.../scripts/harness.py` 已这么做 ✓）。症状是探针答案被几千行 `dispatch …` 淹没 ✓。
- **工具的**输出**要 ASCII** ✗：这段控制台是 GBK ✓，脚本里一句中文段落标题或 `✗` 就能让整个报告**在印出关键清单之前**崩掉 ✓（实测 ✓）；名称/结论放 JSON 与案例文档 ✓，控制台只留 ASCII ✓。
- **把"活读数"钉进断言会让测试随环境变红** ✗（本轮又遇到一次 ✓）：重采 profile 后 `tests/browser/test_navigator_plugins.py` 红在 `connection.effective_type == "4g"` ✗ —— 而随包 profile **就是**默认值来源 ✓（`foundation/environment.py` 的 `DEFAULTS` 读 `data/chrome-environment.json` ✓），这次采集当时的网络报 `3g` ✓ ⇒ 失败与代码无关 ✓，是断言把"采集时那台机器的连接类型"当成了常量 ✗。改成与 `profile["connection.effective_type"]` 比较 ✓（同文件下一行的 `battery.level` 本来就是这么写的 ✓），并保留它真正想守的枚举校验 ✓。与 cookie 那条同类 ✓：**凡是从环境采来的值，断言要拿它与同一个来源比，而不是钉死某个读数** ✓。
- **硬编码的到期时间会让测试自己到期** ✗（顺手修掉 ✓，不在本案范围 ✓，但它在全量回归里挡住了判据 ✓）：`tests/unit/test_cookie_storage.py` 的两条用例把 cookie 的 `expires` 写成 `2026-10-06/07` ✓，**当天 00:00 UTC 之后必然变红** ✓（而同文件的 `test_an_expiry_in_the_past_is_not_kept` 正说明过期即丢 ✓ ⇒ 测试自相矛盾 ✓）。改成 `email.utils.formatdate(time.time() + 365*86400, usegmt=True)` ✓：仍是 RFC 1123、仍带那个逗号 ✓，但不再到期 ✓；用 `formatdate` 而非 `strftime` ✓，因为后者的星期/月份名随 locale 变 ✓。
- **站点上报的请求是"我们的异常"的指纹**：本例 `POST …/eb/<id>/chl_api_message` ×3 正对 3 条 `dispatch error` ✓；修好引擎异常后它随之归零 ✓。
- **trace 是主线**：`, error:` 行数是个位数级的强信号；`answered undefined` 按名字聚合后共同守卫一眼可见。
- **`network download:` 是"有没有发过请求"的唯一见证**：没有它，就无法区分"页面没决定发"与"我们拒了"。
- **两侧交换表要同口径**：`script/5s.py` 与 `chrome_side.py` 报同样三项（交换、cookie、页面状态）才能逐行读。
- **先量再断**：本轮的 `pat` 401、空 cookie 罐两条推断都被 Chrome 侧一次 13 秒的运行推翻 ⇒ 每条结论都要有两侧证据。
- **trace 回调本身就是最好的探针** ✗⇒✓：`Browser(trace=True, trace_callback=…)` 换掉 `log_trace` ✓，
  就能按**名字**计数、按需打 Python 栈 ✓（`script\trace_reflect.py` ✓）—— 比在页面里 hook 可靠得多 ✓
  （页面里改描述符，挑战看得见 ✓：`script\frame_loop.py` 第一版就撞上"帧里 `Reflect` 是数据属性"✓）。
- **卡住的 realm 叫不出来** ✗：它自己的 JS 还在跑 ⇒ `evaluate` 只会在 60 s 后 `RealmBusyError` ✓
  ⇒ 数据必须**从 realm 里推**出来 ✓（`context.glob` 装一个 Python 回调 ✓ —— `script\frame_loop.py`
  的 `__probe_report` ✓；从 132 行那次失败学到：`window` 在 worker 里不存在 ✓，`exec_js` 的第一次
  调用也可能早于全局对象就绪 ✓ ⇒ bootstrap 用 `globalThis` 且只在**被接受**后才记账 ✓）。
- **控制台是 GBK** ✗：报告里出现 `€` 之类的字符会让 `print` 抛 `UnicodeEncodeError` ✓ 整段结论消失 ✗
  （本轮踩到一次 ✓）⇒ 打印前过滤成 ASCII ✓，字符本身留在 JSON/案例文档里 ✓。
- **PS 里别写 Python 一行式**：引号会被吃掉（本轮踩过 ✓），改代码用编辑工具。
- **全量回归必须跑**：改动落在上下文/导航核心路径时，全量回归抓出了 `worker=True` 那处自伤。

### 宽扫读数：一个持有者就能盖住整张表（口径已修 ✓）

- **症状**✦：`surface_diff.py --probe surface_wide` 读 `only-chrome 1299 / only-here 211 / kind 9` ✗，与本日志里记录的 `40/113/2` 差得离谱 ✗。
- **定位**：按持有者排序，**1227 项只来自持有者 `0`** ✓ —— fixture（`RECT_PAGE`）里 `<iframe>` 的 window ✓：我们给 `WindowProxy`（21 项 ✓）、Chrome 给真实 `Window`（1248 项 ✓）。两侧本来就不可比 ✓（跨源时能给的就是那个门面 ✓），但表把它的自身名字当成了"窗口的表面"✗。
- **改法**：探针在枚举 window 自身名字时，给"数字名字 + 自引用 window"的持有者标一条 `__frameWindow` 诊断 ✓（`skills/pyv8c-challenge-parity/scripts/probes.py` ✓）；工具把这类持有者**单列一行、不计入合计** ✓（`examples/5s/script/surface_diff.py` ✓），并在合计下说明排除了几个 ✓。
- **判据**：同一探针读数变为 `only-chrome 72 / only-here 211 / kind 2` ✓（`kind 2` 与本日志记录的 `2` 一致 ✓）；`0  frame window, not comparable` 单独一行 ✓。
- **归因**（四族既有差异 ✓，都与声明面改动无关 ✓ —— 成员总数 10361 与全量测试未变 ✓ 印证）：命名空间被做成函数 ✓（`GPUTextureUsage.prototype` 多 `apply/bind/call/prototype` ✓）、常量落位 ✓（`NodeFilter.prototype`、legacy 工厂类上多 `FILTER_*`/`*_NODE` ✓）、legacy 工厂原型 ✓、`only-chrome 72` ✓。

### `NodeFilter.prototype`：不是口径问题，是接口对象的形状（已修 ✓）

- **症状**：`page.evaluate("NodeFilter.prototype")` 我们给一个 28 项的对象 ✓，Chrome 给 `undefined` ✓；`NodeFilter` 自己也多一项 `prototype` ✓。
- **查源码**（不再靠猜 ✓）：Blink 的 `core/dom/node_filter.idl` 是 **`callback interface`** ✓；三个形状写在同一处 —— `bindings/core/v8/generated_code_helper.cc` ✓：
  - `SetupIDLInterfaceTemplate`：`ReadOnlyPrototype()` + `SetClassName()` + 给 **prototype** 挂 `@@toStringTag` ✓（`Node`/`Element` 走这条 ✓）；
  - `SetupIDLNamespaceTemplate`：把 `@@toStringTag` 挂**自身**（是个 `ObjectTemplate` ✓）⇒ `CSS`/`GPU*` ✓；
  - `SetupIDLCallbackInterfaceTemplate`：**`RemovePrototype()`** + `SetClassName()`，**不挂 tag** ⇒ `NodeFilter` ✓。
- **改法**：`_pyv8` 新增 `IdlSpec.callback_interface` ✓（`src/interfaces/{idl_spec.h,idl_spec.cpp}` ✓），在 `pyclass.cpp` 里照抄 Blink 那条分支 ✓（`RemovePrototype()` 本仓 `iterable.cpp` 已用过 ✓）；web 侧一行 `NodeFilter → {"interface": {"callback_interface": True}}` ✓。
- **判据**：`typeof NodeFilter` 是 `function` ✓、`'prototype' in NodeFilter` 是 **false** ✓、`[object Function]` ✓、`name`/`length` 不可枚举 ✓、16 个常量可枚举 ✓、`new NodeFilter()` 抛 `TypeError` ✓ ⇒ 守护在 `tests/browser/test_surface_members.py::test_a_callback_interface_is_a_function_without_a_prototype` ✓；重测（本侧新跑 ✓、Chrome 侧用上一次两侧运行的参考 JSON ✓）读数 `only-chrome 72 / only-here 186 / kind 2` ✓，持有者从 2046 降到 2045 ✓。
- **回归**：`_pyv8` 全量 **402 passed + 69 subtests** ✓；pyv8c 全量 **1137 passed** ✓；重建 `build_ext --inplace` 156 s ✓。
- **剩下一族已在后续批次修好** ✓：命名空间（`CSS`/`GPU*`）的接口对象改按 `WrapperTypeInfo::kIdlNamespace` 造 ✓ —— 即 `SetupIDLNamespaceTemplate` 那条 `ObjectTemplate` 路 ✓，`typeof CSS` 是 `object`、没有 `prototype`/`name`/`length`、`CSS.supports` 仍是函数 ✓。顺带把两个布尔形状标志收敛成一个**数** ✓：`IdlSpec.definition_kind` 取 `pyv8.DefinitionKind.kInterface`/`kIdlNamespace`/`kWrapperTypeNoPrototype`（0/1/2，缺省 0 = 普通接口）✓，旧的 `as_object` 与 `callback_interface` 两个键消失 ✓（两个布尔量能写出"既是命名空间又是回调接口"，重抽时真出现过 7 例 ✗ ⇒ 单个取值从结构上排除 ✓）；`array` 的词也一并换成位 ✓（`pyv8.ArrayProto.iterator` 1 / `.iterable` 31，0 表示不借 ✓），profile 的 `array_words` 段随之改名 `array_protos` 并同样存数 ✓ —— 口径是**值在线上是数字、名字在 `pyv8.flag`** ✓。守护：`tests/browser/test_surface_members.py::test_a_namespace_is_an_object_without_a_prototype` ✓、`tests/unit/test_idl_extractor.py::test_the_definition_kind_decides_the_interface_object` ✓、`tests/unit/test_interface_flags.py::test_the_documents_vocabulary_is_the_bindings` ✓；判据：编译成员总数 **10361（delta 0）** ✓、编译快照把新数值翻回旧词后**逐项一致（0 差异）** ✓、`_pyv8` **404 passed + 72 subtests** ✓、pyv8c 全量 **1140 passed** ✓。
