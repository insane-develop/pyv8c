# Window、iframe 与同源边界

[返回文档索引](README.md)。实现集中在 [window.py](../pyv8c/web/bom/window.py) 与 [urls.py](../pyv8c/foundation/urls.py)。
这是浏览器行为兼容层，不是可以执行敌意代码的安全沙箱。

## 当前模型

iframe 使用子 Page/realm；WindowProxy 在访问者一侧缓存目标窗口的代理。
访问时动态检查 origin，而不是仅在创建代理时检查一次。
更改 iframe.src 会复用已有子 Page/Window，从而尽量保持 contentWindow 身份；这不代表完整 Chromium Frame 生命周期。

顶层 top、parent、self、window 指向自身；子 frame 的 top 指向顶层，parent 指向直接父页面。
contentWindow、document.defaultView 和数字 frame 索引使用一致的代理映射。

## Origin

- HTTP(S)、WS(S)、FTP 使用规范化 scheme、hostname 和有效端口比较；路径、query、fragment 不参与。
- 主机名进行 IDN 规范化；默认端口与等价的显式默认端口相同。
- about:blank iframe 继承父 origin；顶层 about:blank 使用独立 opaque origin。
- 同 Context 创建的 Blob URL 从注册表取得创建者 origin；不能仅靠 blob 字符串恢复所有权限。
- data 等 URL 使用 opaque origin；相同字符串不保证同源。
- 导航提交更新 origin，旧代理的权限随目标当前状态变化。

## 跨源可见面

| 成员 | 当前行为 |
| --- | --- |
| window / self / frames | 目标代理自身 |
| top / parent | 访问者 realm 内稳定的对应代理 |
| opener | null；没有完整 popup opener 模型 |
| length / 数字索引 | 已创建 iframe 的数量与按文档顺序的子 frame |
| closed | 目标 Page 关闭状态 |
| location | 受限 Location；允许 location/href 赋值与 replace 导航 |
| postMessage | 检查 targetOrigin，异步投递 MessageEvent |
| close / focus / blur | 沿用当前 no-op 行为，不是实际窗口管理 |

跨源 document、navigator、history、screen、Storage、自定义属性及其他敏感操作被拒绝，通常抛 DOMException(SecurityError, code=18)。
iframe.contentDocument 跨源返回 null；Location 的 href/origin/host 等敏感读取不允许。
不允许的赋值、删除和敏感描述符读取也受检查；replace 复用导航队列，不承诺完整 session-history replacement。

## WindowProperties 命名查找

WindowProperties 位于 Window.prototype 与 EventTarget.prototype 之间，不等于给 Window 增加万能 named handler。

- 支持非空元素 id，以及 embed/form/img/object 的非空 name。
- 命名 iframe 优先；唯一元素返回该元素，多个匹配返回随 DOM 更新的 HTMLCollection。
- Window 的自有属性及普通原型属性可以遮蔽动态名称；删除自有覆盖后恢复动态查找。
- 动态名称不出现在 own keys 中；命名描述符 writable/enumerable/configurable 为 true。
- 直接在 WindowProperties 上定义、删除属性被拒绝；原型不可替换，preventExtensions 返回 false。
- symbol、数字字符串和借用其他 realm 的原型有专门处理；借用原型不能绕过 origin 检查。

native 的 property-name callback 绑定到 holder 创建 realm，而不是错误使用当前调用者的文档。
普通 Window 成员使用直接 IDL binding；无论 trace 开关如何，都不应仅为观察安装普通 named interceptor。

## 消息与执行线程

postMessage 检查 targetOrigin 并设置 data、origin、source；支持 onmessage 与 addEventListener。
导航和消息排入目标任务队列，默认 executor 自动推进；测试可使用 run_until_idle 等待。
structured_clone 仅支持当前实现的类型，不能假定完整 transfer 列表或任意循环对象都可复制。

同步同源代理转发使用 native 保存的目标 Context；不要在回调里同步等待另一 executor 或阻塞本 realm 的 loop，避免互等。
跨源检查不应被 trace 跳过；trace 只观察原有回调出口。

## 已知限制

- 未实现 document.domain、完整 sandboxed iframe、COOP/COEP、Origin-Agent-Cluster 和进程级 site isolation。
- 导航没有完整的 Window/Document realm 替换与所有旧引用失效规则。
- WindowProxy 的全部反射、symbol、命名 browsing context 查找不保证与 Chromium 完全一致。
- 普通 JS 对象/数组经 Python 桥转发时，不保证所有引用具有 Chrome 的零复制 identity 语义。
- popup/open/opener、真正焦点/窗口关闭、完整 Location/session history 仍不完整。
- Blob URL 跨 Context 注册表校验与撤销后的 origin 生命周期仍有边界。

当前已有同源模型；应针对这些明确缺口继续完善，而不是重新假定安全模型从未实现。

## 回归入口

- [WindowProperties](../tests/browser/test_window_properties.py)：命名查找、遮蔽、描述符和 realm 归属。
- [Window binding](../tests/browser/test_window_bindings.py)：直接成员、反射和普通操作路径。
- [Window security](../tests/integration/test_window_security.py)：同源/跨源、identity、导航、消息、DOMException。
- [Trace 集成](../tests/integration/test_trace.py)：开启观察不绕过跨源拒绝。
- [native access checks](../../_pyv8/tests/test_window_access_checks.py)：原生访问检查与异常桥接。

原生构建与 ABI 的要求统一见 [开发指南](DEVELOPMENT.md)，不要从旧文档的测试环境推断当前 .pyd 可任意跨版本加载。
