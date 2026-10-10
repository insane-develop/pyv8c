# 网络引擎与参数

[返回文档索引](README.md)。实现入口：[registry.py](../pyv8c/network/registry.py)（注册与校验）、[assembly.py](../pyv8c/network/assembly.py)（按 Browser 选项装配）、[backend.py](../pyv8c/network/backend.py)（一个 context 的网络栈），以及三个引擎模块 [cronet.py](../pyv8c/network/cronet.py)、[curl_cffi.py](../pyv8c/network/curl_cffi.py)、[websockets.py](../pyv8c/network/websockets.py)。
本页讲“用哪个引擎、给它什么参数”；loader、拦截与缓存见 [资源加载与拦截](resource-interception.md)。

## 两个角色

一个 context 的网络是两个可独立选择的部件：**HTTP 引擎**负责请求，**WebSocket 引擎**负责 socket。

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| real_network | True | 是否装配 http_engine / ws_engine 指定的引擎；False 表示不联网 |
| http_engine | curl_cffi | HTTP 引擎：已注册的名字、现成对象（要有 request_sync）或工厂；None 表示该角色不装配 |
| ws_engine | websockets | WebSocket 引擎：已注册的名字、现成对象（要有 websocket）或工厂；None 表示该角色不装配 |
| http_options / ws_options | None | 分别交给两个部件的参数，见下（给现成对象时不能传） |

```python
from pyv8c import Browser, transport_names

transport_names("http")  # ('cronet', 'curl_cffi')
transport_names("ws")    # ('cronet', 'websockets')

browser = Browser()                                             # 默认：curl_cffi + websockets
browser = Browser(http_engine="cronet")                      # 换 HTTP 引擎，socket 仍用 websockets
browser = Browser(ws_engine=None)                               # 只装 HTTP
browser = Browser(http_engine=None)                          # 只装 socket
browser = Browser(http_engine="cronet", ws_engine="cronet")  # 一个引擎两个角色，只构造一次
browser = Browser(http_engine=MyHttpOnly())                     # 现成 HTTP 引擎，socket 照常装配
browser = Browser(real_network=False)                           # 不联网：两个角色都不装配
```

边界：

- 角色互不影响：`ws_engine=None` 只关掉 socket，`http_engine=None` 只关掉 HTTP，`real_network=False` 两个都关（离线）。缺 HTTP 时 `page.net` 与资源加载报“没有 HTTP 引擎”（离线改用 `resource_loader`），缺 WebSocket 时 `new WebSocket` 报“没有 WebSocket 引擎”。
- 每个角色可以给**名字**、**现成对象**（HTTP 要有 `request_sync`，socket 要有 `websocket`）或**工厂**（每个 context 调一次，各拿一个实例）。现成对象已经构造完成，所以给它设置对应的 options 会被拒绝（`TypeError`），不会被静默丢掉。
- `real_network=False` 时不装配任何引擎：名字只是偏好、就此不用，而现成对象会被拒绝（不静默丢弃）。
- 两个角色给同一个名字或同一个对象时只构造/使用一次——否则会多出一个客户端、线程池与连接缓存；此时 `ws_options` 会被拒绝，而不是静默忽略。

## 参数与校验

`http_options` 只交给 HTTP 引擎、`ws_options` 只交给 socket 引擎，每个引擎只消费自己认识的键。字典会被拷贝：之后改调用方自己的字典不影响已创建的 Browser。

| 键 | 谁消费 | 含义 |
| --- | --- | --- |
| max_workers | 两个内置 HTTP 引擎的桥接层 | 桥接线程池大小，默认 4；不转发给客户端 |
| default_headers | curl_cffi | 作为会话选项传入；默认 False，本环境不额外添加请求头 |
| cookies / headers | websockets | 握手是否带 context 的 cookie（默认带）；`headers` 追加握手头 |
| proxy | 三个内置引擎的桥接层 | 出口代理，由 `Browser(proxy=...)`/`new_context(proxy=...)` 解析后注入；空串表示直连。curl_cffi 逐请求传 `proxies`（空串即钉住直连），cronet 在构造时把 `proxies` 交给客户端，websockets 传 `connect(proxy=...)`。见 [USAGE 的网络代理](USAGE.md) |
| 其余键 | 该引擎的客户端 | 按其构造签名转交，如 cronet 的 `timeout_ms`/`verify`、curl_cffi 的 `impersonate`、websockets 的 `open_timeout`/`ping_interval`/`max_size` |

校验发生在**创建 Context 时**（`new_context()`），并且**按角色分别进行**：

| 情况 | 行为 |
| --- | --- |
| 键在该角色引擎的集合里 | 转交该引擎 |
| 拼错的键，或属于另一个角色引擎的键 | `TypeError`，指出键名、角色与引擎名，并列出可接受的键 |
| 引擎在运行时无法列出自己的键 | 不预检，由客户端构造时拒绝，错误里补上它来自哪个 options |
| 宿主注册的引擎未声明键集合 | 不校验 |
| 给了 options 却没有引擎读它 | `TypeError` |
| 注入的引擎填了该角色 | `TypeError`：它已构造完成，不会读这些 options |
| 给了 `proxy=` 但该角色的引擎不声明支持代理 | `TypeError`，指出哪个引擎不能被代理 |
| 给了 `proxy=` 又给了该角色自己的代理键（`proxy`/`proxies`/`proxy_auth`） | `TypeError`，指出该保留哪一个 |

```python
browser = Browser(http_engine="cronet", http_options={"timeout_ms": 5000, "impersonete": "chrome"})
browser.new_context()
# TypeError: unknown option 'impersonete' for http engine 'cronet';
#            accepted: base_url, chrometls, default_domain, headers, max_workers, proxy, proxies, timeout_ms, verify
```

各引擎的键集合来自各自客户端的构造签名：cronet 随已安装 cycronet 版本变化；curl_cffi 把会话参数 TypedDict 只留给类型检查器（运行时不存在），属于上表第三行；websockets 取已安装 `websockets.sync.client.connect` 的具名参数——它末尾的 `**kwargs` 会转交 `socket.create_connection`，因此不计入，那里的拼写错误只会在 socket 线程里炸。
缺少可选依赖仍报 `NetworkUnavailableError`，且先于键校验发生。

## WebSocket 握手

socket 引擎按浏览器的做法发握手：环境派生的 `User-Agent` 与 `Accept-Language`、文档的 `Origin`、以及 context 里该 URL 的 cookie（按 `http`/`https` 等价形式查询，cookie 存的是主机而不是 scheme）；`ws_options={"headers": {...}}` 再叠加调用方自己的头。

```python
browser = Browser(ws_engine="websockets", ws_options={"cookies": False, "headers": {"X-Page": "socket"}})
```

事件是异步的：`new WebSocket(url)` 返回后 open/message/error/close 依次到达页面；页面 `close(code, reason)` 完成关闭握手后，close 事件报告同一对 code/reason 且 `wasClean` 为真；Context 关闭会关闭它开过的全部 socket。

## 自定义引擎

两个角色各有注册入口：

- `register_transport(name, factory, option_keys=..., proxy=False)`：HTTP 引擎。
- `register_ws_transport(name, factory, option_keys=..., proxy=False)`：socket 引擎。

factory 按 `factory(options=<字典或 None>)` 调用，每个 Context 一次；`option_keys` 是惰性可调用，返回该引擎接受的键，不声明就不校验（由宿主自己负责）。HTTP 引擎需要 `request_sync`/`request_async`，socket 引擎需要 `websocket`/`close_websocket`，两者都需要 `close`；`cookies` 由 Context 在构造后赋值。`proxy=True` 声明该引擎会走 `proxy` 选项（值形如 `http://host:port` 或空串表示直连）；不声明即不接受，装配时给了代理会报错而不是把设置丢掉。

```python
from pyv8c import register_transport, register_ws_transport

class MyHttp:      # request_sync / request_async / close
    ...
class MySockets:   # websocket / close_websocket / close
    ...

register_transport("my-http", lambda options: MyHttp(options), option_keys=lambda: frozenset({"region"}))
register_ws_transport("my-sockets", lambda options: MySockets(options))
```

一个对象同时提供两组方法就能同时担任两个角色（cycronet 就是这样），因此在 `http_engine` 与 `ws_engine` 同名时会被复用一次。

## 与每请求参数的区别

`http_options` 是**会话级**的，只在创建 Context 时读一次。单次请求的参数走 `page.net.<method>(..., options={...})`：

```python
page.net.get("https://example.test/", options={"impersonate": "chrome"})
```

每请求参数按请求交给当前 HTTP 引擎，各引擎忽略自己没有对应物的键（cronet 目前只使用其中的 `timeout` 字段，不读 `options`），与 `http_options` 的校验无关。

引擎对 `NetworkRequest.redirect` 的契约：只有 `"follow"` 表示让引擎自己跑完整链，其余取值（`"manual"`/`"error"`）都表示调用方要逐跳检查，因此引擎必须不跟随。
跟随链、方法改写与跳数上限由资源层负责（[redirects.py](../pyv8c/network/redirects.py)）；无法被要求停止跟随的引擎声明 `FOLLOWS_ITSELF`，
资源层对它只走 `follow` 路径（跳数与逐跳检查按引擎自己的能力打折），`manual`/`error` 直接报网络错误，而不是静默照做。

会话级代理由桥接层逐请求下发，因此调用方在单次请求里给出 `proxies`/`proxy`（curl_cffi）时会覆盖它——顺序上后写的胜出，这是刻意的：更靠近请求的一层说话。`page.net.<method>(..., options={...})` 是唯一能这样覆盖的地方，页面内的请求不行。
