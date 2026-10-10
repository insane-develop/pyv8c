# 不做完整 AST 的 JS 逆向

trace 会告诉你"页面**读了什么名字**" ✓，但不会告诉你"**它为什么读、读了会怎样**" ✗。
那部分只能读页面自己的代码 ✓ —— 本页是**在没有 JS 解析器的机器上**也能用的办法 ✓。

> 现状（2026-10-06 实测）：本机 **没有** `esprima` / `pyjsparser` / `tree_sitter` / `slimit`
> / `jsbeautifier` ✓ ⇒ 任何"用 AST 遍历"的写法**都跑不起来** ✗。
> 可选的升级路径写在最后一节 ✓；**主路径不依赖任何解析器** ✓。

## 0. 代码从哪来（先落盘，再离线读）

| 来源 | 内容 | 说明 |
| --- | --- | --- |
| `examples/5s/script/cf_frame/*.js` | 页面/worker 脚本 | 各次运行**自动落盘** ✓（`01-document-*.js` 是页面大包 ✓，`blob-*.js`/`worker-*.js` 是 worker ✓） |
| `…/payload-*.js` | **明文**载荷 | 挑战发给 worker 的原文 ✓（`postMessage` 前的完整字符串 ✓）——**信息密度最高** ✓ |
| 工具 hook 的 report | 各 realm 的 `apis`/`errors`/`workerPosts` | 见 `probe-cookbook.md` ✓ |

**先读 `payload-*.js`** ✓：它们通常就是"探测什么指纹、向哪台主机发什么请求" ✓
（实例：worker 域指纹 + `fetch("https://hagen…")` + `brunhild` 的 headers + 反调试 `debugger` ✓）。

## 1. 混淆的形态（本仓见到的真实样子）

```js
J[lm(p2.J)](yY, N[lm(p2.l)]);            // 取属性名要过一层查表
Y[gO(Wf.e)](`MdFbGFjhNkMTFZKC8gkxplC5VBKaikgky2re7JbIvQs=$21tdQFLp9rkOCcd9JuvWWg==`);
if (J[lm(p2.N)](128, yz)) { n += String[lm(p2.a)](yz); }
```

要点：**属性名不是字面量** ✗，而是 `表.短键` ✓ ⇒
`lm(p2.J)` 的结果就是要调的方法名 ✓ ⇒ 想知道语义，只需把**那张表（含解码函数）算出来** ✓，
不必解析整个 AST ✓。

## 2. 主路径：字符串表 + **让真引擎跑解码器**

思路：**表 + 解码器是自包含的** ✓ ⇒ 把它们抠出来，交给一个真 JS 引擎（pyv8c 自己的 V8 ✓，
或 Chrome ✓）**原样执行** ✓，直接打印 `键 → 名字` 映射 ✓。这比"实现一遍解码算法"可靠得多 ✓
（解码器常有 `charCodeAt`/位运算/字符串反转 ✓，手写必错 ✗）。

```powershell
# 1) 表在哪：这个挑战把整张表**打包进一个长字面量、用 `!` 分隔**（数组/对象字面量那种形状没有 ✗）
python skills\pyv8c-challenge-parity\scripts\js_reverse.py pool cf_frame\05-script-38902.js
#    → pool of 905 parts on '!'  …  matches Set get bind Object runProgram pointer …

# 2) 名字怎么取：看查表函数的使用点，**索引映射就在同一段代码里**
python skills\pyv8c-challenge-parity\scripts\js_reverse.py calls cf_frame\05-script-38902.js JV
#    → m[Jk(HT.N)](cM)  …  HT={m:1500,J:979,l:1484,O:989}      ← 键 → 池下标 ✓

# 3) 常量（主机名 / token / URL）
python skills\pyv8c-challenge-parity\scripts\js_reverse.py strings cf_frame\05-script-38902.js --min 24

# 4) 让 V8 替你算：`--prelude` 可以是文件，也可以是**内联源码**
python skills\pyv8c-challenge-parity\scripts\js_reverse.py run `
       --prelude "var pool=<把 pool 输出粘进来>.split('!');function Jk(i){return pool[i]};" `
       --snippet "Jk(979)+'/'+Jk(989)"
#    → result: matches/get                    （实测形状 ✓）
```

**判据**：解出来的名字必须是**人话** ✓（`matches` ✓、`get` ✓、`addEventListener` ✓）；
一堆乱码 ⇒ 抠错池/抠错下标函数 ✓，回第 1 步 ✗。

> 实测（2026-10-06，`05-script-38902.js` 237 KB）：`tables` **没有**命中 ✗（该包没有"数组/对象字面量表"这种形状 ✗）；
> `pool` 直接给出 **905 部分** ✓ 与 **375 部分** 两个池 ✓；`calls JV` 同时暴露了下标映射对象 ✓；
> `run` 两种 prelude（内联/文件）都通 ✓。⇒ **先跑 `pool`，再跑 `calls` 找映射，最后用 `run` 算** ✓。

## 3. 两个包之间的差异（授权对比 / 版本对比）

```powershell
python …\js_reverse.py diff cf_frame\05-script-38902.js cf_frame\05-script-69040.js
```

它比的是**字符串与符号集合** ✓（不是文本 diff ✗）——同一挑战不同运行/不同版本的包之间，
这个差异往往直接指出"这次多探了什么" ✓。

## 4. 什么时候值得上真解析器

只有当你要做**结构性重写**（批量替换调用点、抽取整个函数体 ✓）时才值得 ✓：

```powershell
pip install esprima            # 纯 Python，够用在"找节点"上
# 或 pip install tree-sitter tree-sitter-javascript   # 更强，但需要编译
```

装了之后仍遵守原则：**先落盘、先离线读** ✓、**结论要有两侧证据** ✓。

## 5. 与 trace 的配合（最有用的一步）

1. trace 里出现 `[FRAME] getter: Navigator.userActivation -> undefined` ✓；
2. 用 `calls` 在包里找 `userActivation` ✓（它是**字面量**时能直接命中 ✓；
   命中不到就说明它来自**表** ✓ ⇒ 走第 2 节算映射 ✓）；
3. 找到的上下文告诉你**页面拿它做什么** ✓
   （例：`typeof navigator.userActivation === 'object'` 这类一致性检查 ✓
   ⇒ 我们在帧里答 `undefined` ✗ 就直接命中检查 ✓）；
4. 于是修法有了**语义依据** ✓，而不是"补一个听起来对的字段" ✗。

## 6. 记录

逆向得到的结论写进**被分析目录的案例文档** ✓（`examples/<case>/README.md` ✓，就地更新 ✓）
与**代码注释/守护测试** ✓，并标注**证据等级** ✓：
`[逆向=实测代码]` ✓ / `[推断]` ✓（本仓被推翻的结论大多是推断被当成了事实 ✗）。
