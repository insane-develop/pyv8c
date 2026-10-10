# 架构与不变量

本文只描述当前源码已经实现的结构。行为以 `tests/` 为最终事实来源。

## 分层

```text
platform          Browser、Page、生命周期、导航、资源、Realm executor、DevTools 宿主
       ↓ capability
foundation        参数原语、错误、URL、任务、生命周期和 Realm 合同
       ↑
web               Feature 实现：DOM、BOM、Fetch、Worker、WebSocket、IndexedDB
       ↑
engine            baseline、FeatureRuntime、IDL 编译、包装器、stub 和 session
       ↓
_pyv8             V8 Context/Isolate、Python 对象桥接、Inspector、trace 原语
```

`web` 只能通过 `foundation` 合同和 `PageServices` 的窄 capability 使用宿主能力；不能导入 `platform` 控制器、保存 `Page`/`Browser` owner 或读取隐式全局页面状态。

## 页面生命周期

`Browser` 持有配置、接口 baseline、网络后端和上下文集合；`BrowserContext` 管理页面、Cookie、Blob URL 和 DevTools 入口；`Page` 组合导航、脚本、资源、任务、生命周期和服务对象。

每个 Page 有一个 `PageRealm`：

- 一个 V8 `Context`；
- 一个 Realm-local `BindingSession`；
- 一个 owner executor，所有页面脚本和普通 Inspector 操作在此执行；空闲时睡在唤醒事件上，朝定时器死线则睡在同线程的 asyncio loop 上；
- 一个 `Window` 与 `Document` 对象。

iframe 使用独立 Page/Realm，通过 `WindowProxy` 跨窗口访问。Worker 使用独立 Worker Realm 和执行器。关闭顺序是先停止新任务与网络，再在 owner 线程释放 Context/Realm，最后释放 Page/Browser 资源。

## FeatureRuntime

Feature 是 Web API 装配的唯一概念。

### 声明

`FeatureSpec` 记录：

- Feature 名称和实现模块路径；
- 提供的完整接口名集合；
- Window 槽位；
- Feature 依赖；
- 是否为 Core。

`FeatureCatalog` 启动时建立不可变的名称索引，不导入低频实现模块。

### 运行时

每个 Browser 拥有独立 `FeatureRuntime`。Python 进程可以共享 `sys.modules`，但 Feature 的 loaded 状态、接口类映射和失败状态不能跨 Browser 共享。

实现模块必须显式导出：

```python
FEATURE = FeatureImplementation(
    {"InterfaceName": InterfaceClass},
    {"windowSlot": lambda window: value},
)
```

Runtime 负责依赖先后、循环检测、接口映射完整性、模块加载失败回滚和同一 Feature 的单次加载。IndexedDB、Navigator、WebSocket、Worker、FileSystem 是 Optional Feature；首次访问全局构造器或 Window 槽位时才加载。以方法为唯一入口的 Feature（旧 FileSystem 的 `webkitRequestFileSystem`）由该方法先 `materialize_interface()` 提交自己的接口组，再向页面交出对象。

### Feature 内对象惰性创建

Feature 模块加载、Realm 接口物化和具体对象实例化是三个独立阶段。加载某个 Feature 只允许准备类和绑定；除页面启动必需的对象外，不能在父对象构造时顺带创建低频子对象。

例如创建 `Navigator` 时不能立即创建 `NetworkInformation`、`BatteryManager`、Geolocation、Plugin 集合或存储配额对象。它们应在对应属性或方法首次访问时创建，后续访问返回同一对象。其他 Feature 中昂贵、低频或可能持有资源的子对象遵循相同规则。

`Window` 槽位和 Feature 内部子对象统一使用 `LazyValueStore`：宿主 getter 只处理可用性并调用 `.value("slot")`，集中式 `_create_value()` 工厂负责构造。需要成组创建的关联值由工厂一次写入同一个 store。不要新增 `_object()` 辅助函数、逐属性 nullable 字段或 `dict.get()` 风格的第二套缓存。

惰性对象工厂不得放在 getter 中重复执行，不得用局部动态 import 代替 Feature 加载，也不得让“未访问”与“已创建但值为空”共享无法区分的缓存状态。构造失败不得留下缓存项，以便后续访问重试。测试必须同时验证首次访问前未实例化、首次访问触发创建和重复访问身份稳定。

### Realm 物化

Feature 模块加载和 Realm 物化是两件事：模块类缓存属于 Browser Runtime，`IdlSpec` 配置属于 Realm Session。Optional Feature 首次使用时，其关联接口组在当前 Realm 一次性提交，避免对象返回时缺少对应绑定。

`_pyv8` 的 lazy factory 只传递接口名称；Python 回调把名称交给 FeatureRuntime，不在原生层复制 Web API 路由。

## 绑定与 baseline

Chromium 生成的 `idl/idl.zip` 只提供接口结构、继承、成员、模板标志和生成签名状态。IDL 编译（含占位实现与 realm 绑定回调）在 `engine/binder.py` 完成。

读它的路径只有两站：`engine/idl.py` 负责加载（压缩文档、边车与二进制缓存）、解码、查找与「一个 realm 要装什么」的计划，`platform/realms.py` 用一次 `Context.install(names)` 把名字装上去。参照浏览器实际装到多少，来自 `data/chrome-environment.json` 里 `bindings` 段的实测（同一模块读它，`tools/collect_environment.py` 在采集环境的那次会话里一并写入），不是手写名单。原生侧 `Context.install` 是唯一的安装入口，每个 IDL 名在 realm 的注册表里一条记录，模板与 spec 都按名索引。

参数转换、缺参判断、重载、异常顺序和 Promise 拒绝由每个 Web API 实现直接负责。禁止新增签名驱动的运行时验证器或统一调用 wrapper。

未实现接口可以由 `Binder` 的桩生成占位实现；stub 不应模拟真实参数语义。
成员实现由 `engine/binder.py` 在编译期合成：`Browser(interface_overrides=...)` 给的宿主钩子在同一层替换回调，
因此不动实现类、不改 baseline，也不影响别的 Browser。

每个基线成员依次问四处，先命中者胜：

| 顺序 | 来源 | 说明 |
|---|---|---|
| 1 | 宿主 override | `Browser(interface_overrides=...)`，检查过是否在基线里 |
| 2 | 类自己的 `__idl_overrides__` | **只写差异**：改名回调、基线没有的成员、`setter`、`replaceable`、基线沉默时的 `length` |
| 3 | 命名约定 | operation → `fn_<成员名>`，attribute → `get_<成员名>`（必需）/`set_<成员名>`（可选） |
| 4 | 桩 | `Binder` 的私有桩，带 `__v8_idl_stub__` 标记 |

位置、property flags、`side_effect`、cross-origin 检查**一律取基线**，attribute 可写性取基线 `member.setter_state`
（基线沉默时才由声明或 `set_<名>` 决定）。所以声明里写这些字段绑定不会读 —— `pyv8c/tools/idl_audit.py`
按这条判据列出"每个声明是否值得一行"，`tests/unit/test_declarations_are_a_difference.py` 守住它。

已编译的 spec 在**可互换**的 bindings 间复用（同基线、同 profile、同类、无宿主钩子、静默桩，且本次编译没报过
不匹配）：一个进程里第二个 Browser 不再重建 10361 个成员 spec（`tools/measure_startup.py` 量它）。

## 资源与调试

资源加载由 `ResourceController` 统一处理 loader、拦截器、缓存、脚本和网络响应。Trace 是被动观察；`resource_interceptor` 是主动改变请求，两者不能混用。
HTTP 重定向也在这一层逐跳跟随（规则表在 `network/redirects.py`，引擎只被要求不要自己跟随），因为一个跳要不要跟、下一跳带什么头、跨源要不要过 CORS 检查，都只有知道页面 origin 的这一层能回答。

DevTools 协议、传输、请求路由和 Inspector 会话核心位于相邻 `_pyv8` 的 `pyv8.devtools`；`pyv8c.platform.devtools` 只提供 Page/iframe/Worker 的宿主描述和关闭回调。

## 修改规则

- 新 Web API 先加入 Feature 声明，再实现显式 `FEATURE`，最后添加行为测试。
- Window 值通过 Feature 槽位创建，不在 Window 中导入低频实现类或维护第二套缓存。
- 跨层需求通过 capability 扩展，不增加 owner/backdoor。
- 先改测试和最小实现，再运行专项测试与完整套件。
- 例外：`window.chrome` 在 baseline 里没有接口，也不是 Window 成员，因此不走 Feature，而是由
  `PageRealm` 在 realm 初始化时用 `web.bom.chrome` 的引导脚本安装（Chromium 同样在页面脚本之前安装它）。
  它必须是普通对象，所以成员函数经临时全局桥接给 JS 后用 `Object.defineProperty` 定义，桥接全局随即删除。

## IDL 词汇表（迁移目标）

这一段是**目标契约**，不是现状：下面每一节在对应批次落地后逐句变成事实描述，名字一旦冻结就不再增补新词。

整条链路只有五个概念：

| 概念 | 管什么 |
|---|---|
| `Idl` | 一份 IDL 集合。加载那个文件，并按名回答一条 `IdlInterface`。 |
| `IdlInterface` | 集合里的一条接口：父、成员、常量、模板、暴露面。 |
| `IdlMember` | 一条成员：名字、种类、位置、以及自身标志的解码。 |
| `Profile` | 参照浏览器实测装到多少（它的收窄就是"哪些名字/成员真的在"）。 |
| `Bindings` | Python 实现类与 IDL 的缝合处：一个名字给一个类、一个类给它的 JS 配置、一个 realm 装什么。 |

只有两个模块：`engine/idl.py`（集合与 profile）、`engine/binder.py`（`Binder`：一条 `IdlInterface` 加一个
Python 类 → 一份 JS 配置；桩、包装、签名都在这里，且都是私有）。名字对照：

| 旧名 | 新名 |
|---|---|
| `baseline.py` | `idl.py` |
| `BaselineRepository` | `Idl.load(path=None)` |
| `BaselineSnapshot` | `Idl` 本身（集合就是快照） |
| `InterfaceTable` / `Interface` | `Idl` 的查询面 / `IdlInterface` |
| `Member` / `LOCATIONS` | `IdlMember`（自带位置与标志解码） |
| `WindowSurface` / gaps / array_protos | `Profile` |
| `universe.py` / `InterfaceUniverse` | `bindings.py` / `Bindings` |
| `_GENERATED` + `class_for` | `Bindings.class_for`（桩类是其私有缓存） |
| `RealmPlan` / `build_plan` / `plan()` | `Bindings.plan()` 返回 `Realm`：`installs`（该装的）与 `eager`（首屏先装的） |
| `assembly.py` / `InterfaceCompiler` | `binder.py` / `Binder` |
| `BindingWrapperFactory` / `SignatureCache` / `StubFactory` | `Binder` 的私有部件 |
| `member_flags` / `property_flags` / `cross_origin_check` | `IdlMember.flags()` / `.cross_origin()` |
| `BindingSession` / `FeatureRuntime` | 名字保留（它们不在 IDL 这一侧） |
| `__idl_spec__`（原生：名与父） | 保留，`@interface(name, parent)` 写入 |
| `__v8_idl_spec__` + `IdlSpec`/`MethodSpec`/`AttributeSpec` | `__idl_overrides__`（只写差异的小映射），经 `pyv8c/idl/spec.py` 适配层落到原生 |

### 覆盖映射写什么

成员项可写 `callback`/`getter`/`setter`（谁回答）、`length`、`side_effect`、`location`
（`"instance"`/`"prototype"`/`"interface"`）与标志 `property_flags`（`"read_only"`/`"dont_enum"`/
`"dont_delete"`）、`receiver_check`、`cross_origin_check`、`cross_origin_get_check`/
`cross_origin_set_check`（`"check"`/`"do_not_check"`）；接口项除拦截器钩子外还有 `iterate`、
`array`、`to_string_tag`、`length` 等。字段名与原生一致，取值用词，web 侧不导入任何 `pyv8.*Spec`。

声明里出现文档根本没有的成员时，判定交给 `baseline_mismatch`：`error` 让页面建不起来，
`warn` 报一次并跳过该成员（这就是"用一份旧文档跑"的样子）。

### 文档之外的类

`WindowProxy`、受限 `Location`、`Image`/`Option`/`Audio` 工厂这类**不属于 IDL 集合**的类，用同一个映射
说全自己的形状，再交给 `standalone(cls)` 落到原生；成员的种类由类自己的体决定（`fn_<name>` 是操作，
`get_`/`set_<name>` 是属性）。这是唯一一条不经过文档的描述路径。

### 落地读数（2026-10-07）

| 判据 | 读数 |
|---|---|
| 编译成员总数（`tests/unit/test_compile_cost.py`） | 1254 接口 / **10361** 成员，与改造前逐项相同 |
| 声明审计（`python -m pyv8c.tools.idl_audit`） | `unresolved 0`、`flags 0`、`no_interface 0`、`renamed 5`、`resolved 143`、`restating 32`、`declared 180 / 13 specs` |
| 全量测试 | 1140 passed / 1 skipped（devtools teardown 偶发抖动是既有的） |
| 冷编译（`tools/benchmark.py --cases cold_compile --runs 5`） | **510 ms**（改造前 471–488 ms，机器噪声 ±10%） |
| 启动（`python -m pyv8c.tools.measure_startup`） | `Browser()` 4.3 ms、首个 `new_page` 199 ms（此前约 445 ms） |
| 迭代表面（`examples/5s/script/iterable_probe.py`） | 两侧逐行一致 |

**一条刻意修正** ✓：`AudioContext.onerror`、`AudioScheduledSourceNode.onended`、
`OfflineAudioContext.oncomplete` 曾声明"无副作用"，而文档说"有" ✗ ⇒ 声明删除，标志改由文档给 ✓
（`flags` 桶因此归零 ✓，守护在 `tests/unit/test_interface_flags.py` ✓）。这三条成员都是接口自己的成员 ✓，
删除声明不影响它们被安装 ✓，页面级行为由 `tests/integration/test_audio_rendering.py` 覆盖 ✓。

**宽扫读数的口径已修** ✓：`surface_diff.py --probe surface_wide` 曾读 `only-chrome 1299 / only-here 211 /
kind 9` ✗，其中 1227 项来自持有者 `0` —— fixture 里 `<iframe>` 的 window ✓：我们按设计给出 `WindowProxy`
（21 项 ✓），Chrome 给出真实 `Window`（1248 项 ✓），HEAD 的 `__v8_index_get__` 同样返回 `_view(...)` ✓
⇒ 与重设计无关，但它盖住了其它差异 ✓。现在探针给这类持有者标 `__frameWindow` ✓、工具单列一行
（`0  frame window, not comparable` ✓）并**不计入合计** ✓。

**接口对象的形状是一个数，不是两个标志** ✓：`IdlSpec.definition_kind` 取
`pyv8.DefinitionKind.kInterface`（0 ✓，缺省：有原型的函数）、`kIdlNamespace`（1 ✓，纯对象）或
`kWrapperTypeNoPrototype`（2 ✓，没有原型的函数），文档里存的就是这三个数 ✓。名字取自
`wrapper_type_info.h` 的 `WrapperTypeInfo`（`kIdlNamespace` ✓，以及决定回调接口的
`kWrapperTypeNoPrototype` ✓），三者**互斥** ✓ —— 两个布尔量能写出"既是命名空间又是回调接口"这种
不可能的组合 ✓（重抽时确实出现过 7 例 ✓），一个数写不出来 ✓。同一批把 `array` 的词也换成了位 ✓：
`pyv8.ArrayProto.iterator`（1）/`.iterable`（31），0 表示不借 ✓ —— **值在线上是数字、名字在
`pyv8.flag`** ✓，`tests/unit/test_interface_flags.py::test_the_documents_vocabulary_is_the_bindings` ✓
守着文档层与绑定层的两套拼写一致 ✓（文档层要能在没有原生扩展时读，所以它自己重复一遍 ✓）。
代价是失败的提示变成了取值范围（`takes an integer in 0..31, not 'iterator'` ✓）——对整数字段统一如此 ✓。

**callback interface 已按源码修好** ✓：`NodeFilter` 在 Blink 是 `callback interface`
（`core/dom/node_filter.idl` ✓），其接口对象由 `SetupIDLCallbackInterfaceTemplate`
（`bindings/core/v8/generated_code_helper.cc`）造出 —— `RemovePrototype()` + `SetClassName()`，
既不 `ReadOnlyPrototype()` 也不给原型挂 `@@toStringTag` ✓。`_pyv8` 原先只有前两条路径 ✗ ⇒
`pyclass.cpp` 里照抄该分支 ✓；抽取器从 `kIdlInterface` 配 `kWrapperTypeNoPrototype` 读出 `2` ✓，
类不必申报 ✓。现在 `typeof NodeFilter` 是 `function` ✓、`'prototype' in NodeFilter` 是 **false** ✓、
`[object Function]` ✓、`name`/`length` 不可枚举 ✓、16 个常量可枚举 ✓、`new NodeFilter()` 抛
`TypeError` ✓ —— 与两侧参考逐项一致 ✓。读数因此从 `only-here 211` 降到 **186** ✓
（`NodeFilter.prototype` 的 24 项 + `NodeFilter` 自己的 `prototype` 1 项 ✓）。

**命名空间同批修好** ✓：`CSS` 与六个 `GPU*`（`GPUBufferUsage` 一族）在 Blink 是 `namespace`
（`kIdlNamespace` ✓，`SetupIDLNamespaceTemplate` 造 **ObjectTemplate** 并只给对象本身挂 tag ✓）⇒
`typeof CSS` 是 `object` ✓、没有 `prototype`/`name`/`length` ✓、`CSS.supports` 仍是函数 ✓，
与 Chrome 一致 ✓ —— 本侧 `Object.getOwnPropertyNames(CSS)` 69 项里没有一项是 Chrome 记录里没有的 ✓
（`only-here` 为空 ✓，而做成函数时那 8 项 `length`/`name`/`prototype` 一族正是多余面 ✓）。旧做法把
"没有原型"当成回调接口的证据 ✗ ⇒ 七个命名空间一度同时被标成两个形状 ✗；
现在抽取器先读种类再读形状 ✓，且**一份碎片没有证据就不再发声** ✓（`CSS` 的 `WrapperTypeInfo` 在
`core/v8/v8_css.cc` ✓、`modules/v8/v8_css.cc` 只是实现碎片 ✗ ⇒ 后者不会把 1 冲成缺省 ✓）。守护在 `tests/browser/test_surface_members.py::test_a_namespace_is_an_object_without_a_prototype` ✓
与 `tests/unit/test_idl_extractor.py::test_the_definition_kind_decides_the_interface_object` ✓。

**重抽要连带重采画像** ✓：画像的一致性门按整份文件的 sha256 判 ✓ ⇒ 文档字节一变就得重采
（`python -m pyv8c.tools.collect_environment --headed --window-size 1920,1080` ✓，同一台 Chrome
154.0.8037.95 重采的 `bindings` 段与旧画像**逐项相同** ✓ —— `keys` 237、`exposed` 1234、
`omits.prototype` 92、`omits.interface` 9、`array_protos` 62 ✓ —— 只有 `connection.downlink`/`rtt`
随采集时刻浮动 ✓）。

余下两族是既有差异 ✓（成员总数 10361 与全量测试未变 ✓ 印证）：常量落位 ✓（legacy 工厂类上多出
`*_NODE` ✓）、以及 `only-chrome 72` ✓。
