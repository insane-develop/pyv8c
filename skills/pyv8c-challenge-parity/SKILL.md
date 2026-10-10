---
name: pyv8c-challenge-parity
description: Use when pyv8c has to behave like a real browser on a live challenge (bot walls, Turnstile/managed challenges, fingerprint doors, heavy front-ends). The loop is trace-first — read the engine's own trace of what the page asked and what it answered, turn every "answered undefined" / "threw" / "never fetched" into a two-sided local reproduction, fix the engine, guard it with a test, and repeat until the challenge passes.
---

# 挑战 parity：用 trace 找差异，修到通过

## 目标（一句话）

**让 pyv8c 在真实挑战里表现得像真 Chrome** —— 做法不是读规范猜实现 ✗，而是
**读 trace 日志 → 找出与真浏览器的差异 → 两侧复现 → 修引擎 → 留回归 → 再跑，直到挑战通过** ✓。

差异的来源只有三类，都会**自己出现在日志里** ✓：

| 类别 | 在 trace/日志里的样子 | 典型处置 |
| --- | --- | --- |
| 页面读到的值是 `undefined` | `[FRAME] getter: Navigator.userActivation -> undefined` | 补实现 / 修守卫条件（**成片出现时先找共同守卫** ✓） |
| 调用抛了 | `[FRAME] method: HTMLDocument.createEvent('TouchEvent') -> undefined, error: <DOMException>` | 补该 API 的既有语义 |
| 该发的请求没发 | 请求清单里**少一条**（`GET …/ci/<ray>`）、或**从不出现在日志里** | 资源发现路径缺失（如 `<link rel=preload>`、`data:` 解码 ✓） |

> 关键判据：**先把"我们这边发生了什么"读出来，再问"Chrome 那边是什么样"** ✓。
> 不要凭现象猜机制 ✗ —— 本 skill 的每一条教训都来自"猜错后被日志纠正" ✓。

## 循环（每一轮都走完这五步）

```
① 抓 trace  →  ② 归类差异  →  ③ 两侧最小复现  →  ④ 修 + 留回归  →  ⑤ 重跑比对
```

### ① 抓 trace（有界运行，日志 = 证据）

```powershell
cd D:\code\pyv8env\pyv8c\examples\5s
$env:PYTHONPATH="D:\code\pyv8env\pyv8c;D:\code\pyv8env\_pyv8"
python 5s.py --trace --seconds 30 --timeout 150      # 建议先删旧 5s.log：mode="w" 会截断，但残留会混淆
```

- **一切运行都要有界** ✓：`--seconds`（页面拿到的时间）+ `--timeout`（子进程被杀的上限）✓；
  探针里一律 **守护线程 + 十秒上限** 包住 `browser.close()` ✓。
- trace 只在 **pyv8c 侧**存在 ✓（Chrome 没有这份日志 ✓）；Chrome 侧的对照靠**探针读数** ✓。

### ② 归类差异（一行命令，别肉眼翻 20 MB ✗）

```powershell
python script\trace_analyze.py 5s.log          # 本 skill 的 scripts/trace_analyze.py
```

它按 `[REALM] kind: NAME -> VALUE` 归类 ✓，输出：

- **读到 `undefined` 的 getter**（按属性名聚合 ✓）——最尖锐的差异信号 ✓；
- **返回 `undefined` 的方法调用**（已排除 void 类 ✓）；
- **带 `, error:` 的行** ✓ —— 我们抛了、真浏览器不抛的地方 ✓（信号最强，通常只有个位数 ✓）。

同一条差异要问三件事：**哪个 realm**（`[TOP]`/`[FRAME]`/`[WORKER]` ✓）、**谁在读**（页面代码位置 ✓）、
**共同守卫是什么**（一群属性同时 `undefined` ⇒ 一定是同一个 `if` ✓）。

### ③ 两侧最小复现（把它压到"零第三方流量" ✓）

**用本地页面复现，不要反复打真实站点** ✓（既低风险，也更快、更稳）：

```python
from harness import _ask_page
left, right = _ask_page("https://probe.test/x.html", SCRIPT, 2.5)   # 同一份脚本跑两侧
```

- 复现的要求：**单变量** ✓（一次只改一个因素 ✓）、**两个见证通道** ✓
  （例如 `onload` + 帧内 `postMessage` ✓；只有一个通道时，"没跑"和"跑了没回"看起来一样 ✗）。
- 拿不准就问"**是它吗**"：把差异的**前提**逐个删掉 ✓（本 skill 反复靠这一步把假象推翻 ✓）。
- 需要真站时：**低频、有界** ✓，并且只用于"这条差异在真实页面确实发生"的确认 ✓。

### ④ 修 + 留回归 + 更新案例文档

- 改动**尽量小且落在既有语义上** ✓（先找"代码自己写的意图" ✓——本轮 `data:` / preload / iframe 三处都是"注释写了该怎么做、代码里没做" ✗）；
- 立刻加守护测试 ✓（`tests/browser/` 或 `tests/unit/` ✓，小、快、**有界** ✓）；
- **更新被分析目录的案例文档** ✓（`examples/<case>/README.md` ✓，写这一条的症因/两侧证据/改法/判据 ✓，就地改而不是追加 ✗，见"记录"一节 ✓）；
- 跑**全量** `pytest tests -q --ignore=tests/repros -p no:randomly` ✓（核心加载/导航路径的改动必须全跑 ✓）。

### ⑤ 重跑比对（判据要事先写下来 ✓）

修完**重跑 ①** ✓，用同一套命令看那几条差异**是否消失** ✓（例：`enumerate` 里 27 个名字应从
"answered undefined" 列表退出 ✓；`error:` 行数应归零 ✓）。**不要用"看起来更好了"** 当判据 ✗。

## 硬性规则

1. **参照必须是 headed Chrome，且不带自动化标记** ✓（`harness.py` 已处理：`ignore_default_args=["--enable-automation"]` + `--disable-blink-features=AutomationControlled` ✓；无头 Chrome 的 UA/可见性不同 ⇒ 结论要打折 ✓）。
2. **两侧条件必须一致** ✓（`harness.py` 先跑 pyv8c 读 `innerWidth/innerHeight`，再用同一视口开 Chrome ✓）。
3. **一次只改一个变量** ✓；**每个结论都要有两侧实测** ✓ 或**两侧都缺的说明** ✓。
4. **报"某功能挂住"之前必须单变量二分** ✗⇒✓，因为**探针自己**也能挂（见"已知的坑" ✓）。
5. **有界** ✓：所有运行都有秒数上限；探针的 `close()` 走守护线程 + 10 秒 ✓。
6. **记录分两处，都别记流水账** ✓：
   - **代码里** ✓：改动注释写"为什么这样修" ✓、守护测试 docstring 写"这条守卫哪个真实差异" ✓；
   - **被分析目录里** ✓：关键发现写进该案子的案例文档 ✓（`examples/<case>/README.md` ✓ 或 `examples/<case>/<case>.md` ✓，见下一节 ✓）；
   - 被推翻的结论要**显式撤回** ✓（写明"为什么错" ✓——本 skill 的坑表里就有 3 条被撤回的假象 ✓）。

## 记录：写进被分析目录的案例文档

**规则**：分析过程中每有一个**结论级**发现 ✓，就更新**被分析目录**下那份案例文档 ✓
（该目录已有的：`examples/5s/README.md` ✓、`examples/px3/px3.md` ✓；没有就新建 `README.md` ✓）。
**不要**另起一份 repo 级台账 ✗，**更不要**按时间顺序流水记账 ✗。

**写什么 / 不写什么** ✓：

| 写 ✓ | 不写 ✗ |
| --- | --- |
| 关键突破（"那句 `reading 'call'` = `eval(TrustedScript)` 从未执行" ✓） | 每一条命令、每一次运行的输出 ✗ |
| 已修差异：症状 + **两侧证据** ✓ + 根因 + 改法 + **判据** ✓ | "今天先跑了 X，又试了 Y，然后…" ✗ |
| 未解决差异 / 已知差异（**带判据** ✓，方便下一轮直接接手 ✓） | 没有结论的猜测清单 ✗ |
| 被推翻的结论 + 为什么错 ✓（防止重复踩 ✗） | 复制粘贴的大段日志/载荷 ✗（放文件路径即可 ✓） |
| 可复用的方法与坑 ✓ | 与本案无关的通用规范 ✗（那属于本 skill ✓） |

**一条发现的样子**（同一件事写一遍 ✓，按"发现"分组，不按时间 ✓）：

```markdown
### <一句话结论：谁在哪个 realm 上，我们怎样、Chrome 怎样>

- 症状：<trace 行原文 / 页面现象>
- 两侧证据：pyv8c `<读数>` ✓ / Chrome `<读数>` ✓（命令：`…`）
- 根因：<哪一行代码的哪个条件>
- 改法：<最小改动，或"未修 ✗">
- 判据：重跑后 <该名字从 undefined 列表消失 / error 归零 / 请求出现>
```

维护方式：**就地更新**已有条目 ✓（新证据补进去、结论变了直接改并注明撤回 ✓），
**不要**在文末不断追加"第 N 轮"✗ —— 案例文档读起来应该是**结论的集合**，不是日记 ✓。

## JS 逆向：直接读页面逻辑（可选但很值）

当 trace 只说"页面读了这个名字"，而你想知道**它为什么读、读了会怎样** ✓ 时，就去读**挑战自己的代码** ✓：

- `script/cf_frame/*.js` 是历次运行**落盘**的页面/worker 脚本 ✓（含 `payload-*.js` ✓ 这些是**明文** ✓）；
- 混淆包用**查表**取属性名（`lm(p2.X)` ✓）⇒ 不必做完整 AST ✗，
  用 `scripts/js_reverse.py` 的**字符串表 + 让真引擎跑解码器**两招就够了 ✓；
- 详见 `references/js-reverse.md` ✓（含"没有 JS 解析器时怎么办" ✓）。

## 工具

| 文件 | 用途 |
| --- | --- |
| `scripts/trace_analyze.py` | 把 trace 归成"答 undefined 的读取/调用 + 抛错行" ✓ |
| `scripts/js_reverse.py` | 逆向 JS（**无需解析器**）：`pool` 拆打包的表 ✓、`calls` 找查表/下标映射 ✓、`strings` 抽常量 ✓、`diff` 两包对比 ✓、`run` 用 V8 算名字映射 ✓ |
| `scripts/harness.py` | 两侧运行器（`run_here` / `run_chrome` / `_ask_page` / `pump` / `_bounded` ✓） |
| `scripts/probes.py` | 探针库（可直接跑或抄 ✓） |

案例里的站点专用工具（`examples/5s/script/`）：`turnstile_realms.py`（hook 各 realm 并读回 ✓）、
`payload_diff.py`（两侧请求/载荷长度对照 ✓）、`5s.py`（真站跑 + verdict ✓）。

## 已知的坑（都被踩过，别再踩 ✗）

| 坑 | 现象 | 正确做法 |
| --- | --- | --- |
| 测量工具自己干扰被测对象 | 主线程 `run_until_idle` 会**等正在被测量的那一轮** ✗ ⇒ 转储变成"恢复之后"的状态 ✗ | 测量时**完全不碰 executor** ✓：主线程只 `sleep` + 读属性 ✓；收尾用 `os._exit(0)` ✓ |
| 探针的 loader 太万能 | loader 对**任何 URL** 都回页面 HTML ⇒ `data:` 帧拿到同一个页面 ⇒ 无限递归 ⇒ 看起来像"引擎卡死" ✗ | loader **只对 `http(s)`** 回页面 ✓，其余 `return None` 交给引擎 ✓ |
| HTML 层提前闭合脚本 | 探针内联 `</script>` 把页面自己的脚本标签闭合 ✗ ⇒ 整页脚本不执行（五种帧全 `{}` ✓） | `json.dumps(...).replace("</", "<\\/")` ✓ |
| 用了不存在的 API | `page.wait_for_timeout` 在 pyv8c 的 `Page` 上**不存在** ✗ | 既有惯例：`page.run_until_idle(timeout=0)` 循环 ✓ |
| 探针读一个通道就下结论 | 只有 `onload` 时无法区分"没跑到" 与 "跑了没回报" ✗ | **两个见证** ✓ |
| PowerShell 管道吃掉证据 | `Substring`/`TrimEnd` 作用在 `MatchInfo` 上会抛错，整行证据消失 ✗ | 打印**原始行**（`"$_"` ✓），裁剪放到 Python 里 ✓；大输出写文件再读 ✓ |
| 用 PowerShell 重写源文件 | `-Raw` + `Set-Content -Encoding UTF8` 会加 **BOM** ✗ ⇒ `SyntaxError: U+FEFF` | 改代码用编辑工具 ✓；确需脚本改文件就用 Python 以 `utf-8-sig` 读、`utf-8` 写 ✓ |

## 参考

- `references/workflow.md` —— 两侧运行器、注入钩子、打点的完整用法 ✓
- `references/probe-cookbook.md` —— 加探针、结论模板、症状短语 ✓
- `references/trace-parity.md` —— **trace 为主的差异分析**（日志格式、判据、分类学 ✓）
- `references/js-reverse.md` —— **不做完整 AST 的 JS 逆向**（字符串表 / 解码器复算 / 调用点 ✓）
- `references/targets.md` —— 怎么挑靶子、差异三分法 ✓

> **记录落在两处** ✓：跨案子通用的东西（本 skill 的这些页 ✓）与
> **案子自己的关键发现**（`examples/<case>/` 下的案例文档 ✓，例 `examples/5s/README.md` ✓）
> 分开写 ✓；代码里另留"为什么这样修"与"守卫哪个差异"的注释 ✓
> （例：`tests/browser/test_frame_documents.py` 的 docstring ✓）。
