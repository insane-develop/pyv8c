# IDL：格式、来源与生成

[返回文档索引](README.md)。运行时读取 [idl.zip](../pyv8c/idl/idl.zip)（单成员压缩文档），加载逻辑见 [idl.py](../pyv8c/engine/idl.py)。
文档记录接口形状（成员签名不进运行时文件），不包含完整浏览器行为实现。

## 当前快照

2026-10-08 本机实读：

| 文件 | 字节 | 是什么 |
| --- | --- | --- |
| `pyv8c/idl/idl.zip`（运行时默认，随包发布） | 129,297 | 压缩文档，恰好一个 JSON 成员（成员名取自归档名：`idl.json`） |
| `pyv8c/idl/idl.json`（边车，首次加载展开） | 2,645,068 | 与归档成员逐字节相同，`Idl.digest` 覆盖的就是这些字节 |
| `pyv8c/idl/idl.pyv8ccache`（本机二进制缓存） | 1,157,559 | pickle 载荷，跳过 JSON 解析与结构校验 |

内容：`interfaces` 1,254 个、成员 10,636 条、常量 2,223 条；metadata 为 `version` 7、
`source`、`chromium_version`（这份文档读自哪个 release）、`report`。边车与二进制缓存是**缓存**，不进版本库也不进包
（`.gitignore` 里写着"Releases only contain the ZIP artifact"）。

生成命令（`chromium_version` 读自检出 `src/chrome/VERSION`，也可用 `--chromium-version` 指定）：

```powershell
python tools/extract_idl.py                              # 默认写 pyv8c/idl/idl.zip
python tools/extract_idl.py pyv8c/idl/idl.json --report   # 明文一份，并打印抽取报告
```

输出路径决定形态：`.zip` 压成单成员归档，`.json` 写明文；两种都能读。归档内成员名随输出文件名
（`idl.zip` → `idl.json`）。版本副本放在仓库的 `idls/<release>.zip`（同样是归档；它旁边的边车与
二进制缓存由 `.gitignore` 挡掉，都不进版本库）：重抽之后把 `pyv8c/idl/idl.zip` 覆盖成
`idls/<该 release>.zip`，两者就保持同内容、同摘要。历史读数（2026-09-30）：154 产物 1,254 接口 /
10,636 成员，145 产物 1,224 / 10,166；145 副本当时用于版本回退测试，现在
`tests/integration/test_baseline_versions.py` 直接从随包文档现造一份旧版。

## 根结构

下面是结构示意，不是已经实现的 Example 接口：

```json
{
  "idl": {
    "version": 7,
    "source": "tools/extract_idl.py",
    "chromium_version": "154.0.8030.0",
    "report": {}
  },
  "interfaces": {
    "Example": {
      "parent": null,
      "tag": "Example",
      "exposed_in": ["Window"],
      "constructor": {"exposed": true, "allow_new": false, "allow_call": false, "length": 0},
      "template": {},
      "members": {"instance": [], "prototype": [], "interface": []},
      "constants": []
    }
  }
}
```

接口名就是 `interfaces` 的键，不另设 identity 对象。成员分组是列表，成员自身通过 kind 区分
attribute/operation。加载时拒绝的错误都在读入或编译前点名：版本不是 `7`（指出用 `tools/extract_idl.py`
重生）、`interfaces` 缺席或为空、条目形状不对、父接口不存在或继承成环、`template.array` 不是
`1`/`31`/缺席、`template.definition_kind` 不是 `0`/`1`/`2`/缺席；`.zip` 里不是恰好一个 JSON 成员、
成员路径不安全（绝对路径或含 `..`）、或文件后缀不是 `.zip`/`.json`，同样当场拒绝。

本文件与 profile 决定了绑定实际读取的一切：成员的位置、property flags、`side_effect`、cross-origin
检查、`constructor.exposed`（暴露成 `has_constructor`）、`constructor.length`、`tag`（`to_string_tag`）与
`template.array`。因此 `__idl_overrides__` 里写这些字段绑定**不会读**，成员的可写性也以成员记录为准；
只有"要与 IDL 不同"时才写那几项（`setter`、`replaceable`、IDL 沉默时的 `length`、改名回调）。2026-10-07
实测：删掉 955 处这类接口级字段声明，编译结果逐项不变（判据见 `examples/5s/README.md`）。

## 字段职责

| 路径 | 含义 |
| --- | --- |
| parent / tag / exposed_in | 父接口、toString tag、允许的 realm 类别 |
| constructor.exposed | 是否允许暴露接口对象，与是否能 new 不相同 |
| constructor.allow_new / allow_call / length | 构造/普通调用许可与 JS Function.length |
| template | immutable_proto、undetectable、global_this、definition_kind、unconstructable 等模板信息 |
| template.array | 该接口从 `Array.prototype` 借了什么：`1` = `pyv8.ArrayProto.iterator`（只借 `@@iterator`）、`31` = `.iterable`（再加 `entries`/`keys`/`values`/`forEach`）或缺席；抽取器从生成的绑定与 IDL 算出（`iterable<>`/`maplike<>`/`setlike<>`，或那四个是 operation），`pyv8c/engine/idl.py` 的 `ArrayProto` 收同一套数 |
| template.definition_kind | 接口对象是三者中的哪一种：`1` = `pyv8.DefinitionKind.kIdlNamespace`（纯对象）、`2` = `kWrapperTypeNoPrototype`（没有原型的函数）或**缺席**（`0`，普通接口）；名字取自 `WrapperTypeInfo` 的 `kIdlNamespace` 与原型形状 `kWrapperTypeNoPrototype`，三者互斥 |
| template.call_handler / access_check | 可调用对象与访问检查元数据 |
| template.handlers | named、indexed、cross_origin.named/indexed 的角色声明 |
| members.instance / prototype / interface | 安装在实例、原型、接口对象上的成员 |
| members[].main_world | 成员是否属于主世界；其他世界的成员只在查表排序时让位 |
| constants | 常量，避免重复放入 interface 成员列表 |

handler 角色包含 get/set/query/del/enum/definer；产物用 1 等存在性标记代替 C++ 符号。
`named`/`indexed` 是接口自己的拦截器，只来自 plain 的 `*PropertyHandlerConfiguration`；由
`SetAccessCheckCallbackAndHandler` 构建的那两份属于 `cross_origin.*`。抽取器用
`idl.report.handler_findings` 守住这条：plain 的 named 家族必须能在接口 IDL 里找到 named getter。
这些标记不是 Python callable；`engine/binder.py` 结合 `__idl_overrides__` 决定最终 handler。

成员只留运行时读的字段：name、kind、length、flags 或 property_flags、setter、receiver、
cross_origin、side_effect、main_world。
`property_flags` 是 `flags` 的整数形式（`V8Attribute` 位掩码），运行时优先用它；抽取器读不出
这段表达式时不给该字段，运行时回落到解析 `flags` 文本。成员在有整数 `property_flags` 时不写
`flags`。setter 为 1 表示有 setter，null 表示只读；安装位置由外层分组决定，不重复编码为位置元组。
常量包含 name、value、location；未成功解析的常量不能凭空补值。

## 运行时不读什么

抽取器只写运行时读的字段：C++ callback 符号、成员签名与重载、常量表达式、world 原名、审查用
原始字段和 provenance 都不进文件。不参与加载的只有 `idl.report`：它留住本次抽取的读数（接口数、
成员数、未解析签名等）供人核对，`tools/extract_idl.py --report` 把它打到标准输出。接口数量、
成员数量和提取成功都不能当作功能兼容率。

仓库根的 `CHROMIUM_VERSION`（当前 154.0.8030.0）是源码获取工具的默认目标；文档里的
`chromium_version` 是**实际**读出这份文档的检出发布号（`<chromium-root>/src/chrome/VERSION`；
读出不来时抽取器拒绝并要求传 `--chromium-version`，不安静地记一个猜来的版本）。
不要从目录名 Cronet145、默认 UA 中的 Chrome/152 或原生 V8 版本反推——那三处都不能证明 release。
更新快照时同时记录源码 commit、生成命令和输出摘要。

## 运行时怎么读它

两站，顺序是读出文档 → 装到 realm 上：

- `engine/idl.py`：加载（压缩文档、边车、二进制缓存）、解码、查找与「一个 realm 要装什么」的
  计划，首次读时算一次并缓存。成员保留文档里的简写（`flags`/`receiver` 等），翻成绑定位是
  `engine/binder.py` 的事。
- `engine/binder.py`：把这些声明编译成原生 IdlSpec，包括成员属性位、占位实现与 realm 绑定回调。
- `platform/realms.py`：一份计划一次 `Context.install(names)` 装到全局上，接口对象的属性统一按
  Chromium 的属性位安装；realm 自己的注册状态（`platform/sessions.py`）回答原生侧对某个名字的问询。

文档列出的是 Chromium 能装的全部成员与接口，而参照浏览器只装其平台与设置允许的子集。
这部分事实在 `data/chrome-environment.json` 的 `bindings` 段，由 `tools/collect_environment.py`
在采集环境的那次会话里从本机 Chrome 实测，与环境的 `source` 共用同一个浏览器与时间戳：

- `keys` / `exposed`：`Object.keys(window)` 与 `Object.getOwnPropertyNames(window)`；
- `omits.prototype`：只看差集——文档在每个接口原型上声明的成员里，参照浏览器没装的那些；没有
  全局构造器的接口（`HTMLDocument`、`DocumentType`、`Text`、`Comment` 等）用 `document`、
  `createElement(...)` 一类实例的 `Object.getPrototypeOf(x).constructor.name` 补上测量；
- `omits.interface`：同样只看差集，但针对 **接口对象自身**（`Object.getOwnPropertyNames(window[X])`）。
  命名空间把成员放在这里而不是原型上——文档声明 `CSS.animationWorklet`、`SubtleCrypto.supports`、
  `TrustedHTML.fromLiteral` 等，参照浏览器并不装；2026-10-07 加这一节之前，`CSS.animationWorklet`
  会被装出来，而它是页面读得到的多余表面（判据见 `examples/5s/README.md`）。命名空间不是函数，
  所以采集脚本对 `typeof window[X] === 'object'` 的名字同样读一次；
- `array_protos`：`{接口: 数}`，数就是 `template.array` 那套——`1`（参照浏览器在这个接口上只发布
  `@@iterator`）、`31`（还发布 `entries`/`keys`/`values`/`forEach`）。测量沿原型链找**自有 descriptor**
  （`Object.getOwnPropertyDescriptor` 逐层上溯），**不读值**：`CSSMathMax.prototype.values` 是 getter（长度）、
  `Cache.prototype.keys` 是异步方法，按名字读值既会答错问题也会跑页面代码，只有 `typeof d.value === 'function'`
  才算迭代器成员。只对文档声明过的接口记录（与缺口同一条规则）；没有 `@@iterator` 的接口**不记录**——
  那正是该字段的第三种取值。若某接口有 `@@iterator` 却只有部分具名方法，采集器**直接报错**而不是二选一
  （2026-10-07 实测：文档声明过的接口里没有这种形状；`Iterator.prototype` 是 `@@iterator` + 仅 `forEach`，
  但它是 V8 内建、文档并不声明）。取值时**实测优先**，其次才用文档自己的；
- `against`：这些缺口是相对哪份文档测出来的（`Idl.digest`）。

`engine/idl.py` 读这一段，`Idl` 用它收窄：`Window` 用窗口面（覆盖它承载的全部位置），
其他接口把缺口里的成员按**位置**去掉——`omits.prototype` 只作用于 `location == "prototype"`，
`omits.interface` 只作用于 `location == "interface"`（原型上有没有某个名字，不能说明接口对象上有没有），
`instance` 位置的成员仍不在测量范围内：它们不在页面对每个接口都拿得到的那种对象上。`array_protos` 不删成员，
只改该接口的 `array` 取值，装配层据此决定向 `Array.prototype` 借哪几个内建。缺口里没有的接口整份保留；
段缺席（旧 profile）或形状不对时按"没测过"处理，行为与不传 profile 时相同；`against` 与当前文档的
`Idl.digest` 不符时画像**整体不启用**并报一次警告，说明要重跑 `tools/collect_environment.py`——
半应用（名字收窄生效、成员省略丢掉）是它要避免的那件事。`Idl.member()` 在祖先链上找不到某名字、
而它又在本接口缺口里时，也不再往子接口里找：参照浏览器没有的名字不该被后代捞回来装上。
被收窄掉的成员仍是文档声明过的，`Idl.declares()` 让编译器把它们当作浏览器之间的差异、静默跳过，
不计入 `discrepancies`；只有文档真的没有的成员才报差异。换成别的 Chrome 版本时重采集即可，
不手写名单。

## 缓存与可信输入

`idl.zip` 首次加载会在旁边展开同名 JSON（边车），并写一份 `.pyv8ccache` 二进制缓存：边车让后续
启动不必解压，二进制缓存再跳过 JSON 解析与结构校验。三者按内容与时间互认——归档比边车新时以
归档为准并重写边车，缓存只对**逐字节相同**的源生效（摘要不符、版本不符、字节损坏都直接重建，
不报错）。本机实测（Python 侧，同进程交错取 min/median）：JSON 解析+校验 37.3/41.0 ms，
二进制缓存 28.3/31.9 ms，归档解压 5.5/5.7 ms（只在首次）。进程内还缓存已解码文档。
源码发布与包数据以 `idl.zip` 为准，本地 JSON/二进制缓存不应当作新的权威文档。

`Idl.digest` 是加载内容（JSON 字节）的摘要：归档与它的边车给出同一个值，记录时不要未经核对
就当作 ZIP 容器字节的 SHA-256。

二进制缓存使用 pickle，只能来自可信本地构建；不要接收外部提供的缓存文件。
损坏缓存可以回退重建，但这不构成对恶意 pickle 的安全隔离。排障时可移走明确对应的本地缓存，
保留原始归档。

## 生成流程

元数据工具可以在没有原生 pyv8 的环境下使用；仍需要 Python 包依赖以及已生成的 Chromium binding 源码。
先用 `--help` 核对本地工具参数：

```powershell
python tools/fetch_chromium.py --help
python tools/extract_idl.py --help
```

源码获取流程会下载 Chromium、处理依赖并生成 binding，不只是读取一个版本号；可能消耗大量时间和磁盘空间。
以下是手动维护命令，不会在普通安装/测试中自动执行：

```powershell
python tools/fetch_chromium.py --version 154.0.8030.0 --root .chromium-build/chromium
python tools/extract_idl.py --chromium-root .chromium-build/chromium --report
```

已有生成目录时可传 `--bindings-dir`；指定 GN 输出用 `--build-dir`。输出路径缺省就是
`pyv8c/idl/idl.zip`；`--report` 把抽取报告打到标准输出。

更新后运行 [提取器测试](../tests/unit/test_idl_extractor.py)、[归档与缓存测试](../tests/unit/test_idl_archive.py)、
[baseline 版本测试](../tests/integration/test_baseline_versions.py)、[IDL 编译测试](../tests/unit/test_idl_compilation.py)
与完整浏览器回归共同验证文档更新。
