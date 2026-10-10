# px3 分析脚本

四个工具加一个共享层。每个都以**同一个挑战页、同一批环境档案、正常模式 Chrome**为准，
输出力求可复现：能给出数字就不要给结论。

```powershell
cd pyv8env\pyv8c\examples\px3
$env:PYTHONPATH="D:\code\pyv8env\pyv8c;D:\code\pyv8env\_pyv8"
```

| 文件 | 作用 |
| --- | --- |
| `common.py` | 共享层：挑战 URL（从 `../px3.py` 读，换 uuid 只改那一处）、Chrome 启动、两侧页面上下文、`pump()` 非阻塞泵、结果比对 |
| `parity.py` | 一批**最小对照页**，一次跑一个用例、两侧并排：`replaced`（替换元素默认尺寸）、`ua`（UA 样式表）、`rect`（`DOMRect`）、`viewport`（视口/文档宽度）、`dom_api`（入口点存在性）、`nodes`（元素在解析/新建/模板/克隆/收养/跨文档各条路径下的读数）、`css`（内联与链接样式表、`@media`/`@supports`）、`frames`（rAF 时间戳与帧数） |
| `challenge.py` | 挑战页本身：`timeline`（控件状态逐秒）、`tree`（控件子树）、`frames`（Chrome 的 frame 列表与其中文本）、`sheets`（样式表与规则数）、`fresh`（换新 uuid 请求） |
| `payload.py` | 抓包/对照/解码：`capture here\|there`、`diff`、`decode`（破解出的编码器实现也在这里） |
| `trace.py` | `log`/`members`/`window` 读 `../px3.py --trace` 的日志；`frames`/`binding`/`style` 在引擎内部打点；`watch` 在页面脚本前注入钩子，看控件 frame 的读数变化（两侧都跑） |

## 用法示例

```powershell
python312 script/parity.py                      # 全部最小对照
python312 script/parity.py css rect --side here # 只看 pyv8c 一侧
python312 script/challenge.py timeline both 30
python312 script/challenge.py tree chrome
python312 script/payload.py capture here --seconds 20
python312 script/payload.py diff
python312 script/payload.py decode
python312 script/trace.py log
python312 script/trace.py window contentDocument
python312 script/trace.py watch 30
```

## 两个必须记住的坑

1. **挑战跑起来后页面不会空闲**：`run_until_idle(timeout=N)` 会抛 `RealmBusyError`。
   用 `common.pump(page, seconds)`（非阻塞 + 截止时间），不要用带超时的阻塞调用。
2. **探针要自带容错**：被 PX 藏起来的节点上 `getBoundingClientRect()` 会返回
   `undefined`、`attributes` 与 `children` 也会是 `undefined`（两个引擎都一样）。
   另外 pyv8c 会把 JS 的 `null` 交回成 pyv8 的 `Null`，直接 `json.dumps` 会失败，探针里先 `String()`。
