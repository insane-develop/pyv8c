# 被动 Trace 与运行时开关

[返回文档索引](README.md)。Trace 用于观察已有浏览器绑定的参数、结果和异常，不用于改写执行。
它与会主动跳过/替换请求的 resource_interceptor 是两个不同功能。

## 开关

```python
from pyv8c import Browser

browser = Browser(traceable=True, stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.trace = True
    page.evaluate("document.createElement('div').id")
    page.trace = False
finally:
    browser.close()
```

- Browser(trace=True) 初始开启；Browser(traceable=True) 初始关闭但允许之后开启。
- 两者都为 False 时，普通页面没有启用观察能力，临时开启会报错；这是配置约束，不是模板必须补装拦截器。
- new_page(trace=False/True) 覆盖新页面初始状态。page.trace 必须赋 bool，关闭页面后不能切换。
- 切换经 owner executor 串行执行，成功后同步 Page、PageServices 与 native Context 状态；不重建对象。
- browser.trace 是后续创建页面的默认值，不广播修改已有页面。建议创建时使用 traceable=True，不以修改 Browser 配置代替页面开关。
- 当前 iframe 创建路径未传父页面 trace 覆盖，使用 Browser 默认值；修改父页面开关不会同步 iframe。
- Worker 在初始化配置时读取所属页面 services.trace，使用 Browser 的 sink；已有 Worker 不随页面切换。

## 自定义回调

```python
from pyv8 import format_trace
from pyv8c import Browser

messages = []

def observe(event, receiver, name, arguments, value, error, is_construct, ctx_type):
    message = format_trace(event, receiver, name, arguments, value, error, is_construct, ctx_type)
    if message is not None:
        messages.append(message)

browser = Browser(trace=True, trace_callback=observe, stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.evaluate("document.createElement('div').id")
    assert messages
finally:
    browser.close()
```

未传 sink 时使用 Loguru。stub_logging 控制未实现成员日志，与 trace 开关无关。
多个 realm 可以共享 sink；回调在事件所属线程同步运行，共享可变状态的同步由调用方负责。

## 事件参数

| 参数 | 含义 |
| --- | --- |
| event | get、set、method、construct |
| receiver | 原生 Python 接收对象；构造事件为类；无法解析时为 None |
| name | 属性、方法、构造器名；索引 handler 为整数 |
| arguments | 方法/构造器参数 tuple；属性事件为 None |
| value | getter/方法/构造结果，或 setter 输入值；get/method/construct 失败时为 pyv8.Undefined |
| error | 成功为 pyv8.Undefined；失败为原异常的 Python 表示 |
| is_construct | 是否通过 new 调用 |
| ctx_type | 原生 Context 类型标签；当前页面路径使用 Window，Worker 使用 Worker |

set 失败时 value 仍保留写入值；不会为了确认写入后状态再次读取属性。
基本值按值传递，原生 Python 对象保留身份，其他 JS 对象以 JSObject 系列引用传递。
数组也不会为日志自动展开成 Python list；引用不是深拷贝快照，关闭 realm 后不要继续访问。
长期保存事件可能延长相关对象寿命，应按需清理缓存。

## 覆盖范围与无副作用边界

观察 Python/JS 实现的 IDL getter/setter、方法、原生构造器，以及实际处理操作或抛错的业务 named/indexed handler。
handler 未处理时，V8 继续原来的查找；观察器不会额外读取后续结果或编造事件。

不保证记录纯 JS 自定义属性/accessor、普通方法属性读取、常量读取、未进入绑定回调的拒绝操作。
例如 element.id 可以有事件，element.extra = 1 不因此产生事件。

原生作用域只读已有参数/返回槽/异常，不调用 Reflect.set、不重新 Get、不重复执行方法。
默认 formatter 仅显示复杂对象类型，不读取属性或调用其 toString；回调返回值忽略，自身 Python/JS 异常隔离。
业务异常继续传播；执行终止时不再派发观察回调。需要诊断 sink 错误时在 sink 内自行捕获记录。

重入计数抑制 sink 内再次产生 trace 事件，但不会撤销用户代码的副作用。
主动打印 JSObject、访问其属性、修改 receiver 或调用 evaluate 仍可能改变状态；保持被动时只保存事件或使用 format_trace。

## 实现与回归

- [realms.py](../pyv8c/platform/realms.py)：装配 native Context 参数。
- [engine/trace.py](../pyv8c/engine/trace.py)：统一 Loguru sink。
- [native trace.h](../../_pyv8/src/tools/trace.h)：RAII 观察、轻量转换、异常隔离和重入保护。
- [集成测试](../tests/integration/test_trace.py)：实际浏览器、开关、延迟接口、Worker 与跨源。
- [native 被动测试](../../_pyv8/tests/test_trace_passive.py)：不重复执行、不触发 Proxy/getter/toString、异常身份与终止恢复。

旧 faker.hook JS 输出协议和类上的 __v8_*_hook__ 方法已移除。不要为补日志重新添加执行转发。
