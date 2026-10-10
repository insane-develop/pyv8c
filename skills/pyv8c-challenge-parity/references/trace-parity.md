# trace 为主的差异分析

> 这一页只说一件事：**怎么从 pyv8c 自己的 trace 里，把"与真浏览器的差异"挖出来** ✓。
> 结论都来自本仓实测 ✓；被推翻的推断也写在这里，防止重复踩 ✗。

## 1. 日志从哪里来

```powershell
python 5s.py --trace --seconds 30 --timeout 150     # 子进程写 5s.log（loguru，level=DEBUG）
```

- `--trace` 才开 DEBUG ✓；不开时 `5s.log` 可能**是 0 字节** ✓（别以为"没日志就是没问题" ✗）。
- `mode="w"` ✓ ⇒ 每次运行**截断重写** ✓ ⇒ 分析前先确认时间戳是这一次的 ✓。
- 探针/工具自己打的 Debug 也会混进来 ✓（`network download: …` ✓）——这些是**请求见证** ✓，很有用 ✓。

## 2. trace 行的形状

```
2026-10-06 20:16:06.551 | INFO | pyv8c.engine.trace:log_trace:10 - [FRAME] getter: Navigator.userActivation -> undefined
2026-10-06 20:15:55.125 | INFO | … - [FRAME] method: SVGCircleElement.setAttribute('cx', 15) -> undefined
2026-10-06 20:09:08.485 | INFO | … - [TOP] method: HTMLDocument.addEventListener('DOMContentLoaded', function(){…}) -> undefined
2026-10-06 20:16:07.459 | INFO | … - [FRAME] getter: HTMLInputElement.maxLength -> undefined
2026-10-06 20:15:50.448 | INFO | … - [TOP] setter: HTMLDivElement.id = 'wrBGy8'
```

- `[REALM]` = `TOP` / `FRAME` / `WORKER`（**先看是哪一个** ✓，成片差异往往只在一个 realm ✓）；
- `kind` = `getter` / `method` / `setter`；
- 只有**带 `->` 的行**才"回答了值" ✓ —— **`setter:` 行没有 `->`** ✓：
  把它当"答了 undefined"会凭空造出几百条假差异 ✗（本仓踩过：一次假的 413 条 ✗，修正后真实值是 42 条 ✓）。

## 3. 三类差异，各自的读法

### (a) 读到 `undefined`

```powershell
findstr /C:"-> undefined" 5s.log | Measure-Object          # 粗筛（含 void 方法，噪声大）
python skills\pyv8c-challenge-parity\scripts\trace_analyze.py 5s.log   # 正确归类
```

读法：**按属性名聚合** ✓，然后找**共同守卫** ✓。

> 实例（2026-10-06）：42 条 / 35 个名字 ✓，其中 **27 个 `Navigator.*`** 全在 `[FRAME]` ✓，
> 共同守卫是 `return self._features.value(X) if self._secure() else pyv8.Undefined` ✓；
> 而 `_secure()` 读 realm 的 URL ⇒ 帧里算成 False ✗。
> 顺这条例子的路径：**bulk 症状 → 共同守卫 → 守卫的输入 → 输入为什么不对** ✓（四步 ✓）。

### (b) 调用抛了 —— 信号最强，通常只有个位数

```powershell
findstr /C:", error:" 5s.log            # 全部"我们抛了"的地方 —— 但**先别**当成差异 ✗
```

> **一条 `, error:` 不等于一处差异** ✗✓：非法参数、规范不允许的调用，Chrome 也抛 ✓。
> 2026-10-06 一晚就撞到两次 ✓：`document.createEvent('TouchEvent')`（Chrome 同样
> `NotSupportedError` ✓，因为 `createEvent` 只认规范那串 legacy 名字 ✓）与
> `replaceChild(undefined, …)`（Chrome 同样 `TypeError`，连措辞都一样 ✓）。
> 判据只有一个 ✓：**把同一个调用两条边各问一次** ✓（`_ask_page` ✓，几十秒 ✓）——
> 名字、消息、返回值任一不同才算差异 ✓。按这条，两次都从清单上撤了下来 ✓，
> 而且第二次还顺手把 `NotFoundError` 的消息前缀对齐了 ✓。

> 实例：整份 24.7 MB trace 里**只有 2 行** ✓ ——
> `ShadowRoot.appendChild(<HTMLIFrameElement>) -> undefined, error: <RealmBusyError>` ✓✗（页面自己插帧时撞重入 ✓）、
> `HTMLDocument.createEvent('TouchEvent') -> undefined, error: <DOMException>` ✓✗。
> **两行都值得修** ✓，而且第二行五行就能修 ✓。

### (c) 该发的请求没发（"从不出现" ≠ "出现了没下载" ✗）

```powershell
findstr /C:"network download" 5s.log        # 我们实际发出的
findstr /C:"/h/b/ci/" 5s.log                # 某条应该在的 URL：0 次 ⇒ 元素/URL 根本不存在
```

> 实例：用户报的 `<link rel=preload as=image href="/cdn-cgi/challenge-platform/h/b/ci/…">`
> 在**我们**的日志里 `/h/b/ci/` **0 次**、`preload` **0 次** ✗ ⇒ 不是"没下载" ✗，
> 而是**这个元素在我们走的流程里从未被创建** ✓（真 Chrome 里才有 ✓）。
> ⇒ 结论要写成**"哪个条件没到"** ✓，而不是"下载失败" ✗。

## 4. 请求计数的正确用法

- 请求**条数会波动** ✓（同一代码 11–16 条 ✓）⇒ **不能当进度计** ✗；
- 该看的是**集合差异** ✓：哪些 URL **只在 Chrome 侧出现** ✓ / 只在 pyv8c 侧出现 ✓（`payload_diff.py` 干这个 ✓）；
- **载荷长度**可以稳定对齐 ✓（实测：两个 `POST /fo/` 恒定**多 85 字节** ✓ ⇒ 系统性内容差异 ✓，
  跨运行一致 ✓ ⇒ 不是噪声 ✓）。

## 5. 交叉印证：让两侧都说话

trace 只描述 pyv8c ✓。**差异的另一半必须自己造** ✓：

| 手段 | 得到什么 |
| --- | --- |
| 本地页面 + `_ask_page` | 同一脚本两侧读数并排 ✓（零第三方流量 ✓，首选 ✓） |
| 真站 headed Chrome | 真实页面在 Chrome 里长什么样 ✓（低频、有界 ✓） |
| 落盘的页面/worker 脚本 | 页面**自己的逻辑** ✓（`cf_frame/*.js`、`payload-*.js` ✓，见 `js-reverse.md` ✓） |
| hook 注入（`turnstile_realms.py`） | 各 realm 里"页面做了什么、我们答了什么" ✓ |

## 6. 判据模板（修前先写下来 ✓）

```
差异：<谁在哪个 realm 读/调了什么，我们答了什么，真浏览器答什么>
两侧证据：trace 行原文 + 复现探针的两侧输出
根因：<哪一行代码的哪个条件>
改法：<最小改动>
判据：重跑 trace 后
      · 该名字从"answered undefined"列表消失 ✓
      · 或 ", error:" 行数归零 ✓
      · 或请求清单里出现该 URL ✓
回归：<新增测试文件 + 用例名>
```

## 7. 记录纪律

- 结论级发现写进**被分析目录的案例文档** ✓（`examples/<case>/README.md` ✓ 或 `<case>.md` ✓，没有就新建 ✓）——
  **就地更新**该案子的条目 ✓，不要按轮次往文末追加流水 ✗（写法见 `SKILL.md` 的"记录"一节 ✓）；
- 同时**落在代码与测试里** ✓：改动的注释写"为什么这样修" ✓、守护测试的 docstring 写"这条守卫哪个真实差异" ✓；
- **被推翻的要显式撤回** ✓（在案例文档或注释里写明"为什么错" ✓，例如探针的 loader 太万能 ✓）；
- 结论里**区分"实测"与"推断"** ✓（本仓多条推断被后续实测推翻 ✓）；
- 别把"某个数字变了"当结论 ✗（先看它是否在噪声范围内 ✓）；也**别把没结论的尝试写进去** ✗。
