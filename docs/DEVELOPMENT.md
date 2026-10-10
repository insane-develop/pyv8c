# 开发、构建与验证

`pyv8c` 与相邻 `_pyv8` 是两个独立仓库。Python 包安装、原生扩展构建、Chromium baseline 生成分别处理。

## 环境

```powershell
Set-Location D:\src\pyv8env\pyv8c
$env:PYTHONPATH = 'D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
python -m pip install -e ".[dev]"
python -c "import pyv8, pyv8c; print(pyv8.__file__); print(pyv8c.__file__)"
```

运行时必须能导入相邻 `_pyv8` 的 `pyv8` 扩展。修改 `_pyv8` 的 C++、V8、Inspector 或 trace 时，在相邻仓库单独构建并运行其测试；不要把本地非发布构建当作正式 abi3 产物。

## 代码工作流

1. 先查看 `git status --short`，保留已有工作区修改。
2. 用 `rg` 找到入口、调用者、相关测试和文档链接。
3. 判断修改属于 platform、foundation、engine、web Feature 还是 `_pyv8` 原生层。
4. 先补专项测试，再修改实现。
5. 检查 capability 边界、Feature 所有权、Realm 物化和关闭顺序。
6. 运行专项测试、完整测试和 diff 检查。

## 常用验证

```powershell
python -m pytest -q -n auto
python -m pytest tests/unit/test_lazy_interface_modules.py tests/integration/test_lazy_interfaces.py -q
python -m pytest tests/integration/test_indexeddb.py -q
python -m pytest tests/browser/test_devtools.py -q
python -m pytest tests/unit/test_idl_extractor.py tests/unit/test_idl_compilation.py -q
git diff --check
```

**测试默认并行**（`pytest-xdist`，dev extras 里）：`-n auto` 压的三套 1235 passed 实测 105.7 s → 19.9 s（16 核）。
单文件或少数用例不要加——进程启动开销反超收益。`tests/repros` 保持串行（见下节）。并行下时序敏感的用例
（`race`、少数语料）会因机器负载假失败，所以并行跑出的失败要去掉 `-n` 串行复跑确认。相邻 `_pyv8` 的套件
很小，并行与串行无实质差别（403 passed，5.42 s vs 5.55 s）。

Feature 全量结构校验使用：

```python
from pyv8c.platform.bootstrap import validate_feature_catalog
validate_feature_catalog()
```

## 策略检查

提交前跑一次，五层闸门：

```powershell
python tools/check_policy.py                  # 工作区 vs HEAD
python tools/check_policy.py --base main      # 与分支比较
python tools/check_policy.py --strict         # 把"实现改动没配测试"的告警当成失败
python tools/check_policy.py --record-startup # 接受当前启动时延作为新预算
```

- `layers`：分层规则，委托 `tests/unit/test_architecture.py`，不在此重复实现。
- `companions`：`pyv8c/` 下改了 `.py` 却没有任何 `tests/` 文件随之改动时告警。
- `startup`：与 `tools/startup_baseline.json` 记录的预算比较（中位数 +10% +5ms）。测量的是新
  进程里 `Browser() -> new_context() -> new_page() -> set_content()` 的耗时与导入模块数；换
  机器、或有意改变启动开销后需要重新记录。
- `offline`：把 `_pyv8` 从 `PYTHONPATH` 上拿掉后跑 `tests/unit`，确保它不依赖原生扩展——离线
  抽取器 `pyv8c/tools/extract_idl.py` 就是在这种环境里用的。需要扩展的用例统一
  `pytest.importorskip("pyv8")` 并打 `@pytest.mark.native`。
- `duplicates`：脚本相似度（阈值 0.80）提示"`tests/repros/` 之外的测试在重复语料用例"。语料用例
  同时与真实 Chrome 对照，所以重复的那份该删；因为相似度只是启发式，默认只告警，`--strict` 才失败。

## 行为语料与真 Chrome 对照

`tests/repros/` 里的每个用例声明「页面 + 一段脚本 + 这段脚本必须观测到什么」，同一份用例跑两
遍：一遍在 pyv8c，一遍在**本机已安装的 Chrome**（不下载、不驱动 Playwright 自带的 Chromium）。

```powershell
python -m pytest tests/repros -q -rs     # -rs 显示被排除的用例及原因
```

这一套**不要加 `-n`**：它驱动本机 Chrome，那半边对线程与时序敏感（并行实测 29 failed、321 s；串行
272 passed、17 s）。整套跑时 Chrome 对照那半边的 `greenlet.error: Cannot switch to a different thread`
是既有环境问题——单独跑该文件正常——并行只会把它放大。

- `corpus.py` 的每个 `Repro` 里 `expected` 是 pyv8c 的答案，每次运行都被断言；`expect_error` 用于
  该抛错的用例。
- `harness.py` 用 `PYV8C_CHROME` 指定的 `chrome.exe`，未设置时按常见安装路径查找；找不到 Chrome
  或未安装 `playwright`（`pip install playwright`）时，对照那一半整体跳过，pyv8c 那一半照跑。
- 两侧的观测只有「值」或「抛出的错误类」参与比较：错误消息是写给人的，不比较。
- 本机已知差异写在用例的 `known_divergence` 上并带原因，报告中作为 excluded 列出，不算通过也不
  算失败；差异消失时该用例会失败并要求删除这条排除，所以排除清单不会腐坏。
- 语料要小、每例只问一个角落，且两引擎都能回答。新增用例时先看它在 pyv8c 侧的真实观测，再决定
  是修正预期、还是记一条 `known_divergence`。
- `-rs` 列出的排除项就是当前的已知缺口清单：要引用时看运行输出，不要在本文件里再抄一份。

## Baseline 更新

运行 `tools/fetch_chromium.py` 和 `tools/extract_idl.py` 前确认 Chromium 版本、生成目录和输出路径。baseline 不应依赖人工 review JSON；抽取器只使用 Chromium 生成元数据。更新后运行抽取器、baseline、IDL 编译和完整浏览器测试。

生成 Blink bindings 只需生成、不必编译整棵树：`gn gen out/X` 加
`ninja -C out/X third_party/blink/renderer/bindings:generate_bindings_all`。Windows 上 VS 或 Windows SDK
装在非默认路径时，用 `vs2022_install`、`WINDOWSSDKDIR` 指定路径，并设 `DEPOT_TOOLS_WIN_TOOLCHAIN=0`
走本机工具链，否则 `gn gen` 会去找 Google 下发的固定工具链。

抽取器的 `_webidl.report.handler_findings` 列出"生成代码声明了 named handler、但接口 IDL 没有 named
getter"的条目。cross-origin 的配置由 `SetAccessCheckCallbackAndHandler` 构建，只能归入
`handlers.cross_origin`，不能计入接口自己的 named/indexed——否则全局对象会凭空多出一套 named handler。
（另外，Blink 会为只声明 named 属性的接口生成 indexed 转发回调，所以 indexed 家族不参与这条检查。）

## 性能和并发

不要用未经测量的“提速”描述替代基准。Feature 模块应在首次使用时导入，Browser Runtime 状态不能跨实例共享；Realm Session 配置不能跨页面共享。网络线程不得同步等待页面 owner 线程，页面回调内不得阻塞本 realm 的 loop，关闭操作必须幂等。loader/interceptor 回调默认在 owner 线程，开启 `threaded_loaders` 后在池线程上，此时不得回调 realm。

`tools/benchmark.py` 是这句话的落点：11 个用例覆盖启动、页面开合、跨桥调用、DOM 构建/解析/查询、事件派发、子资源加载与常驻内存，全程离线、各自新建 Browser，每个用例报中位数与最小值。

```powershell
python tools/benchmark.py --record     # 接受当前数字为基线，写 tools/perf_baseline.json
python tools/benchmark.py              # 与基线对比，中位数与最小值双双变慢的用例会标出来
python tools/benchmark.py --cases dom_query --runs 5
```

两次测量之间机器负载不同，差异可能落在噪声里；要看结论，先用 `git stash` 量一次改动前再量改动后。`resident_pages` 的 MB 数是进程提交内存的差值，偏粗；同一条里的“每页 Python 句柄数”是精确值（`Context.live_handles`，原生侧另有 `Isolate.live_v8_handles` 可在关闭前确认句柄归零）。

按这个口径实测过一轮（2026-09-28，`git stash` 回 HEAD 对照）：`dom_query` −57%、`dom_build` −48%、`host_call` −33%、`resource_batch` −23%、`dom_parse` −3%，启动中位数 259 → 245 ms（导入模块 435 → 394）。跨桥一次约 3.7 µs 降到约 1.9 µs，其中原生派发只占 0.18 µs——宿主方法体才是跨桥成本所在，所以先查 Python 侧再看 C++。

## 交付

最终说明列出实现变化、测试命令和结果、环境限制、未处理失败以及是否创建 commit。删除代码或文档时说明原因；不恢复历史日志文件，不修改任务范围外的 IndexedDB、DevTools、trace 或相邻仓库改动。
