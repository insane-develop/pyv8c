# AI 接手开发规范

这份文档是给后续 AI coding agent 的工作协议。它描述当前架构、修改边界、验证要求和常见陷阱；源码与测试优先于本文件。

## 开始工作前

1. 在 `D:\src\pyv8env\pyv8c` 执行 `git status --short`，保留用户已有改动，不使用 `git reset --hard` 或覆盖式 checkout。
2. 同时确认相邻 `D:\src\pyv8env\_pyv8` 是否有原生改动；除非任务明确要求，不把两个仓库混成一次修改。
3. 设置：

   ```powershell
   $env:PYTHONPATH = 'D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
   ```

4. 先用 `rg` 搜索入口、调用者和测试，再修改文件。不要根据旧文档猜测架构。

## 架构不变量

- `Browser -> BrowserContext -> Page -> PageRealm` 是宿主生命周期主链。
- Web API 只能依赖 `foundation` 合同和 `PageServices` capability，不能反向导入 `platform` 控制器、`Page` 或 `Browser`。
- `FeatureCatalog` 只保存声明；`FeatureRuntime` 才拥有模块加载状态。每个 Browser 有独立 Runtime，Python `sys.modules` 的进程级缓存不等于 Browser 状态。
- Feature 实现模块必须显式导出 `FEATURE`，接口映射必须完整；不要重新引入 `vars(module)` 扫描、注册表冻结后写入或散落的 loader。
- Optional Feature 首次接口/Window 槽位访问时加载；同一 Feature 在一个 Browser 中只加载一次，在一个 Realm 中原子物化其关联接口。
- Window 值必须通过 Feature 槽位创建并保持对象身份；不要重新引入由 Window 自己维护的 `_lazy_bom` 或局部动态 import。
- Feature 内部的低频子对象必须在对应属性或方法首次使用时实例化。Window 槽位用 `LazyValueStore`，由 `FeatureSpec.window_slots` 声明、`FeatureImplementation.window_values` 构造；owner 自己持有的多条槽位（Navigator、Screen、Navigation、URL、Worker 全局与 Window 的 `[SameObject]` 成员）统一用 `LazyValueStore({name: factory})` 表：getter 只做可用性判断并调用 `.value("name")`，构造留给表里的工厂。子类只允许 Window 槽位那一个（构造权属于 Feature，须经 runtime 路由）；不要新增 `_object()`、逐属性 nullable 字段或 `dict.get()` 缓存。重复访问必须返回同一实例；构造父对象时不得预先创建 NetworkInformation、BatteryManager、Geolocation、Plugin 集合、存储配额等低频对象。
- 参数语义、转换顺序、同步异常和 Promise 拒绝属于 API 实现；不要增加签名驱动的统一校验 wrapper。
- 一个 realm 的拆卸不得在另一个 realm 的线程上等待：worker 的 `stop()` 把清理投递给 worker 自己的线程并立即返回（`web/workers/runtime.py`），页面/框架拆卸因此不会被 worker 的延迟拖住。`RealmExecutor.call` 的预算是用来报告"某个 realm 卡死"的，不是让一个 realm 等另一个；需要同步回收的只有宿主自己的资源。
- worker 的源码按来源分线程读：页面自造的来源（`blob:`、`data:`）在构造线程读——一次查表、无 I/O——否则页面同轮 `revokeObjectURL` 会赢过延迟到 worker 线程的解析（浏览器在构造期就取走脚本）；网络来源仍留在 worker 线程读，页面 realm 绝不等加载。
- `baseline_mismatch` 默认 `warn`：手写实现与所选 baseline 的成员/接口差异记一次警告并跳过，所以跨版本（例如 145）运行不会因为小偏差挡住装配。怀疑自己实现写错时才切到 `error`——此时手写实现声明的成员、接口与父接口必须能在 baseline 里找到，否则装配当场失败。被容忍的差异可以读出条数：`Bindings.discrepancies`，浏览器级计数在 `Browser.startup_stats["baseline_discrepancies"]`。
- 基线声明了 named/indexed（含 cross-origin）handler 而类没声明的，由 `Binder` 的 handler 桩补占位——**以基线为唯一依据，类不设豁免**；漏声明会当场暴露，而不是静默丢掉拦截器。占位在普通属性探测中就会运行（`Window` 与 `WindowProperties` 的 named 占位同样会跑），而 loguru 首次使用约 75ms、被刻意推迟到第一条日志，所以占位默认静默，只有 `stub_logging="verbose"` 时按 (接口, 回调) 报一次 "待实现"。唯一例外是 cross-origin 访问检查 `__v8_cross_origin_check__`：它是许可判定，占位会让类没打算守的路径被拒，所以仍只给无手写实现的类补。
- `idl/idl.zip` 提供结构、继承、成员和模板元数据，不是运行时参数调度器。
- 产出随包数据的离线脚本（接口 baseline、环境 profile）实现在 `pyv8c/tools/`，仓库根 `tools/` 只放同名壳：壳只从同名模块转发 `main`，逻辑不写在壳里；域包不得 import 它们。`tests/unit/test_architecture.py` 守卫这条配对。

## 修改方法

- 新增 Web API：先确定所属 Feature、接口组、依赖和 Window 槽位，再写实现；在模块尾部之外定义显式 `FEATURE` 映射，并添加最小行为测试。若接口返回低频子对象，测试首次访问前未实例化、首次访问创建和重复访问身份稳定。
- 修改绑定：检查 `engine/idl.py`、`engine/binder.py`、`platform/sessions.py` 与 `_pyv8` 的 lazy factory 协议——spec 必须随工厂的回答在建模板之前到达，失败的批次不留下任何已暂存的东西。
- 修改页面生命周期：沿 `PageServices` capability 传递，不给 Web 对象增加宽 owner/backdoor。
- 修改 IndexedDB、DevTools、trace 或资源系统时，只改变任务指定的子系统；避免顺手重写无关模块。
- 删除代码前先用 `rg` 检查所有 import、测试、命令行入口和文档链接；删除后运行专门测试再运行完整套件。

## 验证清单

```powershell
python -m pytest -q -n auto
python tools/check_policy.py
git diff --check
```

`tools/check_policy.py` 是提交前的五层闸门：分层规则（委托 `tests/unit/test_architecture.py`）、
实现改动是否配了测试、启动时延是否超出记录预算、`tests/unit` 在**没有原生扩展**的路径上仍能运行
（这是离线抽取器 `pyv8c/tools/extract_idl.py` 的使用前提，`--gates offline` 会隐藏
`_pyv8` 跑一遍），以及有没有测试在重复语料用例（`--gates duplicates`，脚本相似度启发式，只告警；
`--strict` 才失败）。启动这一层的数字属于记录它的机器，换机器或有意改变启动开销后用
`--record-startup` 重新记录。

按时间成本选范围（marker 见 `pyproject.toml`）：

- 不需要原生扩展的快速通道：`python -m pytest tests/unit -m "not native"`。
- 跳过长跑与并发回归：`python -m pytest -m "not slow and not race"`。
- 并发/时序回归重复跑（需 dev extras 里的 `pytest-repeat`）：`python -m pytest -m race --count=5`。
- `slow` 默认不跑：真实浏览器采集、重跑整个 unit 套件、V8 堆上限都在这一档。要跑它们用 `-m slow`，
  跑全量用 `-o addopts=--strict-markers`。
- **默认并行**：`python -m pytest -n auto`（`pytest-xdist` 在 dev extras 里）。16 核实测三套 1235 passed：
  105.7 s → 19.9 s。单文件或少数用例不必加——进程启动开销会反超收益；`--dist loadfile` 更慢，别用。
- `tests/repros` **保持串行**：它驱动本机 Chrome，那半边与线程/时序敏感（并行实测 29 failed、321 s，串行
  272 passed、17 s）。整套跑时 Chrome 对照那半边的 `greenlet.error: Cannot switch to a different thread`
  是既有环境问题（单文件跑正常），不是并行造成的，但并行会把它放大。
- 时序敏感的用例（`race` 与少数语料）在并行下会因机器负载假失败：并行跑出的失败要串行复跑确认
  （去掉 `-n` 再跑一次）才算数。
- `_pyv8` 的套件很小，并行与串行无实质差别（403 passed，5.42 s vs 5.55 s），统一带 `-n auto` 即可。
- 夹具范围：只读取页面的模块用模块级 `shared_page`（把每例约 200 ms 的 isolate/页面建立降为每模块一次，
  `test_atob`/`test_btoa` 实测 18.3 → 1.8 s）；会写文档、全局、事件，或断言 Browser 级状态的模块仍用函数级 `page`。

按改动范围额外运行：

- Feature/懒加载：`tests/unit/test_lazy_interface_modules.py`、`tests/unit/test_window_lazy_bom.py`、`tests/integration/test_lazy_interfaces.py`，以及方法入口 Feature 的接口组物化：`tests/browser/test_filesystem_api.py`。
- IndexedDB：`tests/integration/test_indexeddb.py`。
- DevTools：`tests/browser/test_devtools.py`。
- 绑定编译和 IDL 文档：`tests/unit/test_idl_compilation.py`、`tests/unit/test_idl_extractor.py`、`tests/integration/test_baseline_versions.py`。
- Web API 行为：`tests/repros/`（声明式语料 + 本机 Chrome 对照）。改动 `pyv8c/web/` 下任何接口成员后跑 `python -m pytest tests/repros -q -rs`；被排除的用例都带 `known_divergence` 原因，实现补齐后该用例会失败并要求删除排除项。新增行为时优先往语料里加一例，而不是只写一条本地断言。
- 页面可观察行为的断言以语料为准：`browser/` 与 `integration/` 的测试若与某个语料用例重叠，只保留语料覆盖不到的那部分，并在注释里写明各自归属（例如“href/hash 归语料 `anchor_href_resolves_against_the_document`”）。`python tools/check_policy.py --gates duplicates` 会提示疑似重复。

报告测试结果时给出实际命令和通过/失败数量，并注明是并行还是串行；区分代码失败、环境缺少 `pyv8`、原生构建失败和既有无关工作区改动。

## 不要做的事情

- 不恢复 `WORKLOG.md`、`HANDOFF.md` 或一次性验收记录来替代测试和源码。
- 不新增第二套参数验证器、第二套 Feature loader、兼容旧内部路径的转发层或隐式全局 Page 状态。
- 不为了“清理”删除用户未提交的 IndexedDB、DevTools、trace 或其他无关修改。
- 不在没有明确请求时创建 Git commit、推送远程、重建正式 abi3 产物或修改相邻仓库。
- 不把本地测试构建宣称为发布构建。

## 交付格式

最终说明应包含：实现结果、关键文件/行为变化、测试命令及结果、未处理的环境或无关失败、是否创建 commit。若删除文件，说明删除原因；若保留用户脏改动，明确指出未触碰。
