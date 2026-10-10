# px3 挑战：pyv8c 对照 Chrome 的完整记录

对象：PX 的验证码页
`https://www.skyscanner.com.au/sttc/px/captcha-v2/index.html?url=Lz8=&uuid=…&vid=…`
（`uuid`/`vid` 在 `px3.py` 顶部，换一次就是一次新的挑战）。

方法始终是同一条：**两侧跑同一件事，拿可复现的数字对齐**。参照一律是本机
**正常模式** Chrome 154（无头是另一个页面，现象都不同），pyv8c 用同一批环境档案。
分析脚本都在 `script/`（见 `script/README.md`），数据文件也放这里；`cd pyv8c/examples/px3`
之后 `python312 script/xxx.py …` 即可。

---

## 0. 结论摘要

**没有在自动化里拿到"通过"的判定信号**：跑完 `px3.py` 后页面仍停在 captcha 地址
（通过与否由 PX 服务端判定，页面离开 captcha 才叫通过）。用户侧 IP 后来被放行——那时
服务端已不再下发挑战，两个引擎都看不到可操作的按钮，所以"通过"这件事无法由两侧对照来确认。

真正拿到的是**一批可复现的引擎缺陷**，每一个都有最小复现页 + Chrome 实测：

| # | 缺陷 | 影响面 |
| --- | --- | --- |
| 1 | `requestAnimationFrame` 完全没按刷新率节流（3 秒 18 万帧） | 任何传感器/动画采集 |
| 2 | `performance.now()` 暴露原始精度而非 100 µs | 时间粒度本身就是指纹项 |
| 3 | `element.style` 只枚举 9 个原型成员（Chrome 748 个） | 样式前缀/引擎判定 |
| 4 | `speechSynthesis.getVoices()` 恒为空 | 语音列表指纹 |
| 5 | 替换元素没有默认 300×150；行内空盒被拉成整行宽 | 布局与画布指纹 |
| 6 | `getBoundingClientRect()` 返回普通对象而非 `DOMRect` | 指纹方常检测 |
| 7 | `p`/`h1`/`ul` 等被当成行内元素（全挤在一行）；UA 外边距全缺 | 整页布局 |
| 8 | **链接样式表（`<link rel=stylesheet>`）从不被解析** | 任何按 CSS 布局的页面 |

回归：`pytest tests` → 1054 passed；`pytest tests/repros` → 562 passed
（只剩已知的 `navigator.mediaDevices` 差异）。

---

## 1. 请求层：完全一致

9 个请求的 URL、方法与顺序逐一对齐（含那条不常见的 `HEAD captcha.js`），cookie 从
`_px3=` 到 `_pxttld=1` 走同一条路径。抓法见 `script/payload.py capture`。

## 2. 挑战的形态

- 页面自身的 UI 是"H1: Are you a person or a robot?" + 一个含 uuid 的 SECTION。
- 需要人操作时，页面在 `#px-captcha` 里放一个
  `<iframe title="Human verification challenge">`（无 `src`，带 `token`），
  挑战应用在其中渲染出 **PRESS & HOLD** 按钮（截图 `shots/challenge.png`，Chrome 量到 468×100）。
- 父文档**看不到**这个 frame 的内容：元素对象被 PX 自己替换成一层"读什么都得到 `undefined`"的包装
  （见第 6 节，两引擎一致，不是缺陷）。

## 3. 载荷编码（已破解）

`POST /rf8vapwA/xhr/assets/js/bundle` 的体是 `payload=<token>&pc=…&rsc=n`，
token = **`base64(XOR50(JSON))` 里按位置插入 20 个字符**：

1. `json` 是 PX 自己的序列化，形如 `[{"t":<加密类型>,"d":{<加密字段名>:<明文值>}}]`
2. `b64 = base64(每个字符码 ^ 50)`，对应 `agent.js` 里的 `kv(x, jT)`，`jT = 50`
3. 插入位置：`key = (base64(uuid) 的字符码) ^ 10`（`xq = 10`，uuid 取页面 URL 里那个），
   `p_i = key[i % L] * key[floor(i/L)+1]`；超过 `len(b64)` 就按 `top` 线性压缩到
   `[0, len(b64)-1]`，重复值逐个 +1，最后排序；位置是 **1-based**
4. 被插入的 20 个字符 = `(base64(<13 位数字种子>) 的字符码) ^ 10`
5. `xx()` 只插入、不替换

验证：从 token 与已知明文对齐反推出的插入串异或 10 得 `MTYwNDA2NDk4NjAwMA==`，
base64 解出 `1604064986000`；用 uuid 当密钥算出的位置与对齐结果完全一致；两侧四个包
全部解出合法 JSON。实现与用法见 `script/payload.py decode`。

单个字段还有一层：`agent.js` 里 `pr = 4210`，`qw(t) = iS(kv(t, pr))` 即
`base64(每个字符码 ^ 4210)`，凡经 `qw` 的都是 U+1000 段开头的字符串。
例：`Z1ZVXSI3Ums=` → `Microsoft Yaoyao - Chinese (Simplified, PRC)`。

## 4. 载荷对照

| 包 | pyv8c | Chrome |
| --- | --- | --- |
| `rsc=1` 体 / token / 解码 | 1066 / 800 / 584 B | 1118 / 852 / 624 B |
| `rsc=1` 记录字段数 | 17 | 19 |
| `rsc=2` 体 / token / 解码 | 13892 / 13152 / 9848 B | 15468 / 14720 / 11023 B |
| `rsc=2` 记录字段数 | 230 + 20 | 233 + 20 |

两侧各跑两次都稳定（pyv8c 1066/13892 与 1066/13904；Chrome 1118/15468 与 1118/15452），
所以这是**系统性缺口**，不是等待时长造成的。多轮取交集后稳定的缺口只有三个字段
（`script/data/fields.txt` 是解码出的字段表）：

| 记录 | 字段 | Chrome 的值 | 判断 |
| --- | --- | --- | --- |
| `KVQbX2wwF2g=` | `ajsYMC9XHQo=`、`GwppAV5pajA=` | 一对约 1~2 秒的毫秒数，相差 0~2 | 冻住 `performance.now`/`Date.now`/`Date.prototype` 后照样逐次变化，形态像"距导航的耗时"读两次，来源未定位 |
| `egsIQDxqBHI=` | `Tl98FAs+eCA=` | `webkit` | 落在 PX 的错误上报组里；样式前缀统计已与 Chrome 完全一致（各 151），window 上 `webkit*` 成员也一致，仍未定位 |
| `egsIQDxqBHI=` | `DFk+Ekk6PSE=` | 896 字符 | 高熵加密或指纹块：解出 671 字节、238 种取值，单字节异或最好 41% 可打印，无 gzip/zlib 魔数，套同一套载荷编码也不成 |

两侧页面都报同一个 PX 内部错误（`main.min.js` 里的 `nc` 与 `reading 'width'`），
所以缺口不是 pyv8c 页面报错造成的。另外 `px3.py --trace` 里 `待实现` 命中 **0 次**，
PX 没调用任何未实现的成员。

## 5. 已修好的引擎缺陷

### 5.1 `requestAnimationFrame` 当成立刻到期的定时器

原实现把它当 delay 0 的定时器：同样 3 秒 pyv8c 跑 **183153** 帧、Chrome **171** 帧。
改成合成器式帧时钟——一帧一个定时器、同轮注册合并到同一帧并共享帧起始时间戳、
帧内再注册落下一帧、间隔锁在 16.667 ms 网格不漂移。对照：同轮两回调时间戳相同、
帧内注册落下一帧、3 秒 179~181 帧对 Chrome 165~166；px3 全流程顺带从 30 s 降到 22.6 s。

### 5.2 `performance.now()` 粒度

原来暴露 `perf_counter` 原始精度（约 0.02 ms），Chrome 量化到 0.1 ms（实测 4000/4000 都是
整十分之一）。`Clock.now_ms()` 现在量化到 100 µs，探针读数 0.0999 对 Chrome 0.1000。
粒度本身就是指纹项。

### 5.3 `element.style` 枚举

只有 9 个原型成员；Chrome 有 748 个名字且顺序稳定。补齐后数量 748/748、
`getOwnPropertyNames` 750/750、`webkit` 前缀各 151、推导出的引擎名都是 `webkit`。
细节：不把已声明属性提到前面，也不枚举自定义属性。

### 5.4 `speechSynthesis.getVoices()`

恒为空数组；Chrome 是**异步到齐**的（实测 `voiceschanged` 在 272 ms 触发）。
现在两边加载时都是 0、之后都是 3，语音的 uri/name/lang/local/default 逐项相同。

### 5.5 布局：`DOMRect`、替换元素、行内空盒

| 项 | 之前 | 现在（= Chrome） |
| --- | --- | --- |
| `getBoundingClientRect()` | 普通对象 | 真正的 `DOMRect`：`[object DOMRect]`、`instanceof`、`toJSON`、`Object.keys` 为空 |
| `iframe`/`canvas`/`video`/`object`/`svg` 默认尺寸 | `0x0` 或撑满行宽 | **300×150**（iframe 再加 UA 的 `2px inset` 边框 = 304×154） |
| 行内级空盒（无 src 的 `img`） | 被拉成"剩余行宽" | 0 |

### 5.6 布局：UA 样式表

`p`/`h1`~`h6`/`ul`/`ol`/`dl`/`blockquote`/`figure`/`pre`/`hr`/`fieldset`/`li` 等原来被当成
**行内**元素，全挤在 y=0 一行；UA 外边距也全缺。现在块级堆叠（`li`/`summary` 为
`list-item`），并补上 `body` 8px、`p`/`ul`/`ol`/`dl` 16px、`blockquote`/`figure` 16px 40px、
`ul`/`ol` 左内边距 40px、`dd` 左 40px、`pre` 13px、`hr` 8px、`h1`~`h6` 按 em 折算的 px。

顺带确认 pyv8c 报的 `innerWidth 1904×929` 正是环境档案里记的 `viewport.width/height`，
视口本身没问题；两处旧测试断言的 `div.offsetWidth === innerWidth` 与
`body.offsetWidth == 1904` 只在"body 无外边距"时成立，已改成 Chrome 也成立的写法。

### 5.7 链接样式表从不被解析（本轮）

症状：同一个页面 pyv8c 的 `#px-captcha` 是 `1888x0`、Chrome 是 `468x100`→`468x102`。

根因链：

- `discover_element_resource` 按**标签名**登记资源（`<link>` 的 kind 是 `"link"`），而
  `complete_document_resource` 只在 `kind == "style"` 时把正文存进 `node._stylesheet_text`——
  取回来的 CSS **从来没被保存**（请求本身是对的，日志里有 `GET …/main.2fe002ab.css`）。
- 级联 `layout._parse_style_elements` 只扫 `<style>` 元素；`CSSStyleSheet.cssRules` 也只读
  owner 的 `textContent`（`<link>` 为空）。

修法（三处都很小）：存 CSS 的分支改成 `kind in ("style", "link")`；新增共用的
`css.stylesheet_source(node)`（`<style>` 读正文、`<link rel=stylesheet>` 读 `_stylesheet_text`），
级联与 `cssRules` 都走它；样式表晚到时 bump 一次 `document.style_version`，让级联缓存失效。

验证：

| | 修前 | 修后 = Chrome |
| --- | --- | --- |
| 链接样式表 `cssRules.length` | 0 | 3 |
| `.px-loading-area` 盒子 | `1888x19` | **`310x100`** |
| `#plain { color: red }` | `rgb(0,0,0)` | `rgb(255,0,0)` |
| 真实页面 `#px-captcha` | `1888x0` | **`381x0`**（`box-sizing: inherit`、`margin: auto` 等也解出来了） |

最小复现与命令：`script/parity.py css`。

## 6. 已排除的假设（别再走一遍）

1. **控件 iframe"读不出来"不是 pyv8c 的缺陷。** 同一个监视器（`script/trace.py watch`）
   在 Chrome 上给出**逐字相同**的读数：`tagName`/`attributes`/`parentNode`/`childNodes`/`style`
   全 `undefined`、`ownerDocument !== document`、`contentDocument` 为 `undefined`、
   `Object.getOwnPropertyNames(frame).length = 373`、原型与 `instanceof` 都正确、
   `ownSample` 正好是接口整条原型链的 IDL 属性。`--trace` 里可见页面自己对每个属性先
   `getter` 再 `descriptor`，然后自己装到实例上——**PX 反探测，把 frame 元素藏起来**。
   症状出现的时刻与任何被包的 DOM 变更方法都无关（同步语义都验过），换 uuid、重启都不变。
2. 下列最小复现在 pyv8c 里与 Chrome 逐字一致，因此都不是触发条件：子文档收养、
   无 window 的 `createHTMLDocument` 文档收养、子框架向父文档 append、父节点/父 iframe
   被子 realm 读取（含该 realm 随后被关闭）、iframe 移出再插回、在类从未 stage 过的
   realm 里新建 iframe、构造/派发各类事件、`postMessage` 与 `MessageChannel`、
   模板内容/`cloneNode`/`importNode`/`Range` 片段里取节点。
3. 接口物化是**惰性**的（`script/trace.py binding`）：页面开始时 configs 里只有基础链
   （`Node`/`Element`/`HTMLElement`/`EventTarget`/`Document`），`HTMLIFrameElement` 要等原生侧
   来问才 stage；本页里这次询问发生在事件派发过程中——这是正常机制，不是 bug。

## 7. 未解决 / 下一步

1. **`#px-captcha` 的高度差**：挑战可见时 Chrome 是 `468x102`（计算 `height:102px`），
   pyv8c 是 `381x0`（计算高度为空）。宽度已从 1888 修到 381，高度仍为 0，说明还有一条
   给容器定高的规则没生效——先查它来自哪张样式表（`script/challenge.py tree/sheets`）。
2. **`getComputedStyle` 的 `margin-*` 返回空字符串**：`script/parity.py ua` 里
   `p` 的 `margins` 是 `"   "`，而同一元素 `padding` 正常（`ul` 是 `0px 40px`）。
   布局用的是解析后的值（`body` 8px、`p` 16px 都生效），所以只影响读计算样式的页面；
   Chrome 在这些位置返回 `16px` 之类。`border-width`/`border-style` 也同样是空串。
3. 载荷里那三个字段（第 4 节）。
4. `crcldu.com/bd/auditor.js` 的来源未查。

## 8. 运行

```powershell
cd pyv8env\pyv8c\examples\px3
$env:PYTHONPATH="D:\code\pyv8env\pyv8c;D:\code\pyv8env\_pyv8"

python312 px3.py --timeout 300            # 跑一遍挑战（等按钮→按住→松开→看结果）
python312 script/parity.py                # 全部最小对照（两引擎）
python312 script/parity.py css rect       # 指定用例
python312 script/challenge.py timeline both 30
python312 script/payload.py capture here --seconds 20
python312 script/payload.py diff
python312 script/trace.py log
```

注意：挑战真正在跑时页面**不会进入空闲**，`run_until_idle(timeout=N)` 会抛
`RealmBusyError`；`px3.py` 与脚本里的 `common.pump()` 都改成非阻塞泵 + 截止时间，
探针要自带容错（`getBoundingClientRect` 等在被 PX 藏起来的节点上会返回 `undefined`）。

## 附录 A：环境档案

`pyv8c/data/chrome-environment.json` 记的是用
`--window-size=1920,1080` 启动的 Chrome：`viewport.width/height = 1904/929`、
`screen 1920×1080`、`platform Win32`、`language zh-CN`、`webgl` 为 AMD Vega 8 + ANGLE D3D11。
pyv8c 报的 `innerWidth/innerHeight` 与它一致，所以这一层不需要改。

## 附录 B：数据文件

- `script/data/fields.txt`：解码载荷后逐字段的对照表（哪些稳定缺口）。
- `agent_js/`：PX 页面侧脚本（`captcha.js`、`main.js`）与内核 `agent.js`，
  编码器的证据；可从 `client.px-cloud.net/PXrf8vapwA/main.min.js` 与页面 `<script src>` 重新取。
- `shots/`：两侧截图。
- `px3.log`：最近一次 `px3.py --trace` 的日志（`script/trace.py log` 读它）。
