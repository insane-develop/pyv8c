# 请求头与 Chromium 对齐

[返回文档索引](README.md)。实现入口：[request_headers.py](../pyv8c/platform/request_headers.py)（唯一真源）、三个接入点 [resource_loading.py](../pyv8c/platform/resource_loading.py)（导航/子资源/fetch/XHR）、[net.py](../pyv8c/platform/net.py)（`page.net`）、[services.py](../pyv8c/platform/services.py)（WebSocket 握手）。

pyv8c 发出的请求头在**名称、取值与排列顺序**上与 Chromium 一致。取值全部来自 pyv8c 自己的 environment（`user_agent`、`languages`、`client_hints.*`），因此与页面报告的 `navigator.userAgent`、`navigator.userAgentData` 自洽——不借用任何第三方静态指纹。

基线是本机 Chrome 154.0.8037.95：用本地回声服务记录 `self.headers.items()` 的**到达顺序**，两侧跑同一页面与同一段脚本。对照用例是 `tests/repros/test_request_headers_vs_chrome.py`，顺序基线由 `tests/integration/test_request_headers_on_the_wire.py` 逐场景钉住。

## 按场景的头

每种场景一套：`Sec-Fetch-Mode` 与 `Accept` 由 destination 决定，`Sec-Fetch-Site` 由发起方与目标的 origin/site 关系决定。

| 场景 | destination | Mode | Accept |
| --- | --- | --- | --- |
| 顶层导航 | `document` | `navigate` | `text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7` |
| iframe | `iframe` | `navigate` | 同导航 |
| script | `script` | `no-cors` | `*/*` |
| stylesheet | `style` | `no-cors` | `text/css,*/*;q=0.1` |
| image | `image` | `no-cors` | `image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8` |
| audio / video | `audio` / `video` | `no-cors` | `*/*` |
| fetch / XHR | `empty` | `cors` | `*/*` |

无条件出现的：`User-Agent`、`Accept-Language`、`Accept-Encoding`、`Sec-Fetch-Site`、`Sec-Fetch-Mode`、`Sec-Fetch-Dest`，以及三个低熵 client hint `sec-ch-ua` / `sec-ch-ua-mobile` / `sec-ch-ua-platform`。

按条件出现的：

- `Upgrade-Insecure-Requests: 1`：导航（含 iframe）。
- `Sec-Fetch-User: ?1`：**浏览器发起**或**用户手势发起**的导航。`page.goto()` 是，`page.click()` 触发的提交也是；页面里 `location.assign()` 与脚本 `form.submit()`、`element.click()` 不是。
- `Referer`：非浏览器发起的请求，默认策略 `strict-origin-when-cross-origin`——同源给完整 URL，跨源只给 origin（带尾斜杠）。
- `Origin`：非 GET/HEAD 总是带；GET/HEAD 只在 CORS 且跨源时带。实测：同源 GET fetch 不带、同源 POST 带、跨源 GET 带、表单 POST 导航带。取的是**发起文档**的 origin，跨源提交也给自己的。
- `Content-Type`：带 body 的导航（表单提交）按 `enctype` 声明，位置在 `Upgrade-Insecure-Requests` 之后、`User-Agent` 之前。
- `Cache-Control: max-age=0`：非 GET/HEAD 的导航（表单提交）。
- `Connection: keep-alive`：仅 HTTP/1.x。HTTPS 默认协商 h2，因此不发。
- `Range: bytes=0-` 与 `Accept-Encoding: identity;q=1, *;q=0`：媒体元素，一次取回可 seek 的字节。

`Accept-Language` 按 Chromium 的规则生成：首项隐含 q=1.0，其后 0.9/0.8…，逗号后不留空格（`net/http/http_util.cc:788`）。`languages` 为 `["zh-CN", "zh"]` 时即 `zh-CN,zh;q=0.9`。

`Host` 与 `Content-Length` 属于传输层，真源不产出它们。

## 顺序

顺序不是装饰：能按名称集合与值比对，也能按行比对。真源返回**保序 dict**，传输层按插入序生成 `HTTPHEADER`，因此自定义头的顺序就是线上顺序（已由集成用例逐场景断言）。

Chromium 用两套顺序，加上媒体与 WebSocket 各一套：

- 导航族（`document`、`iframe`）：`Cache-Control` → `sec-ch-ua` → `sec-ch-ua-mobile` → `sec-ch-ua-platform` → `Upgrade-Insecure-Requests` → `Content-Type` → `User-Agent` → `Origin` → `Accept` → `Sec-Fetch-Site` → `Sec-Fetch-Mode` → `Sec-Fetch-User` → `Sec-Fetch-Dest` → `Referer` → `Accept-Encoding` → `Accept-Language`。`Cache-Control`、`Content-Type` 与 `Origin` 只在表单提交这类请求上出现，位置就是 Chrome 放它们的位置（实测 Chrome 153）。
- 资源族（子资源、fetch、XHR）：`sec-ch-ua-platform` → `User-Agent` → `sec-ch-ua` → **调用方自己的头** → `sec-ch-ua-mobile` → `Accept` → `Origin` → `Sec-Fetch-Site` → `Sec-Fetch-Mode` → `Sec-Fetch-Dest` → `Referer` → `Accept-Encoding` → `Accept-Language`。
- 媒体族：`sec-ch-ua-platform` → `Accept-Encoding`（identity 那份）→ `User-Agent` → `sec-ch-ua` → `sec-ch-ua-mobile` → `Accept` → `Sec-Fetch-*` → `Referer` → `Accept-Language` → `Range`。
- WebSocket 握手：`Host` → `Connection: Upgrade` → `Pragma: no-cache` → `Cache-Control: no-cache` → `User-Agent` → `Upgrade: websocket` → `Origin` → `Sec-WebSocket-Version` → `Accept-Encoding` → `Accept-Language` → `Sec-WebSocket-Key` → `Sec-WebSocket-Extensions`。

调用方自己设的头（`fetch({headers})`、`setRequestHeader`、`page.net(headers=...)`）替换同名默认头，并按上表资源族的位置插入——这是 Chrome 放下 JS 提供头的位置。

## 已知差异

无法逐字节复刻的项，逐条给出原因：

1. **`Host` / `Connection` / `Content-Length` 的位置由 libcurl 决定**。`Host` 在最前（与 Chrome 一致）；调用方给的 `Connection: keep-alive` 与 body 的 `Content-Length` 会被 libcurl 移到末尾，而 Chrome 把它们放在第 2、3 位。名称与值都正确，只有位置不同，且不由 pyv8c 决定。
2. **HTTPS 一律不发 `Connection`**。真实判定取决于协商到的协议版本，而 pyv8c 在发请求前不知道：用 scheme 近似（https → 按 h2）。若某个 https 站点最终停在 HTTP/1.1，Chrome 会发 `Connection: keep-alive` 而 pyv8c 不发。
3. **不请求 `/favicon.ico`**，也不发跨源 CORS 预检（`OPTIONS`）。这两项是"少一个请求"而不是"少一个头"，按约定不在范围内。
4. **`sec-fetch-site` 的 site 判定不含 Public Suffix List**：用 scheme + 末两级标签近似，因此 `a.co.uk` 与 `b.co.uk` 会被判成 `same-site`。`same-origin` 的判定是精确的。
5. **WebSocket 握手的顺序由 `websockets` 库决定**：头名与取值齐备，顺序与 Chrome 不同。
6. **`Accept-Encoding` 声明 `zstd` 依赖传输层的解压器**。本机 curl_cffi 自带 libcurl 8.21.0，含 zlib / brotli 1.2.0 / zstd 1.5.7，四种都能解；换成不含 zstd 的构建时，声明照发（以"像 Chrome"为准），此时 zstd 响应可能未解压。
7. **module script 的静态 import 是 `Sec-Fetch-Mode: cors`**（与 Chrome 一致），但 pyv8c 只为模块导入传了 `destination="script"`；`page.net` 则没有"被浏览器发起"这一事实，建模为 fetch（`empty` + `cors`）。
8. **`page.net(cookies=False)` 会发一个没有 Cookie 的请求**，真实浏览器不存在这种请求。
9. **`Accept-Language` 只有 environment 给的语种**：`Browser(environment={"user_agent": "Async UA"})` 这类自定义 UA 不含 `Chrome/<版本>` 时，派生出的 client hint 版本号为空字符串，与页面 `navigator.userAgentData` 报告的一致（两者同源）。

## 复现对照

```bash
python -m pytest tests/unit/test_request_headers.py -q                  # 真头表单测
python -m pytest tests/integration/test_request_headers_on_the_wire.py -q  # 线上顺序
python -m pytest tests/repros/test_request_headers_vs_chrome.py -q      # 与真 Chrome 对照
python -m pytest tests/unit/test_form_submission.py -q                  # 表单条目与编码表测
python -m pytest tests/integration/test_form_submission_on_the_wire.py -q  # 提交的请求行/body/头
python -m pytest tests/repros/test_form_submission_vs_chrome.py -q      # 表单提交与真 Chrome 对照
```

对照用例需要本机装有 Chrome（`PYV8C_CHROME` 可另指一个 `chrome.exe`）与 `playwright`（`reference` extra）；两者缺一即 skip。
