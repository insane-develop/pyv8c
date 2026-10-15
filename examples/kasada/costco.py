"""本地执行 Costco SDK，搜索 banana，使用 Loguru 打印 24 条商品关键字段。

以实时搜索接口和商品数据校验作为成功条件。
不读取 HAR、历史 Cookie 或浏览器生成的验证参数。
"""
import json
from contextlib import contextmanager
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import urlsplit
import zlib

WORKSPACE = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(WORKSPACE / '_pyv8'), str(WORKSPACE / 'pyv8c')]

import pyv8
from loguru import logger
from pyv8c import Browser
from pyv8c.web.bom.chrome import ChromeBindings
from pyv8c.web.bom.window_proxy import WindowProxy
from pyv8c.web.page_errors import PageTypeError

PROXY = 'http://127.0.0.1:6696'
KEYWORD = 'banana'
PAGE_URL = f'https://www.costco.com/s?keyword={KEYWORD}'
API_URL = 'https://gdx-api.costco.com/catalog/search/api/v1/search'
HEADERS = {
    'content-type': 'application/json',
    'client-identifier': '168287ea-1201-45f6-9b45-5bbea49f8ee7',
    'client_id': 'USBC',
    'locale': 'en-US',
    'searchresultprovider': 'GRS',
}
SDK_HEADERS = ('x-kpsdk-cd', 'x-kpsdk-ct', 'x-kpsdk-h', 'x-kpsdk-v')


def search_body():
    return {
        'visitorId': str(secrets.randbelow(10**38)).zfill(38),
        'query': KEYWORD, 'pageSize': 24, 'offset': 0, 'orderBy': None,
        'searchMode': 'page', 'personalizationEnabled': True,
        'warehouseId': '1-wh', 'shipToPostal': '98101', 'shipToState': 'WA',
        'deliveryLocations': [
            '115-bd', '1-wh', '1250-3pl', '1321-wm', '1456-3pl', '283-wm',
            '561-wm', '725-wm', '731-wm', '758-wm', '759-wm',
            '847_0-cor', '847_0-cwt', '847_0-edi', '847_0-ehs',
            '847_0-membership', '847_0-mpt', '847_0-spc', '847_0-wm',
            '847_1-cwt', '847_1-edi', '847_bosch_1474-edi', '847_d-fis',
            '847_ge_sea-edi', '847_lg_n1f-edi', '847_lux_us01-edi',
            '847_NA-cor', '847_NA-pharmacy', '847_NA-wm', '847_ss_u362-edi',
            '847_wp_r458-edi', '951-wm', '952-wm', '9847-wcs',
        ],
        'filterBy': ['HIDE_OUT_OF_STOCK'], 'pageCategories': [],
    }


def extract_products(data):
    inventories = {str(item['productId']): item for item in (data.get('inventoryResponse') or [])
                   if item.get('productId') is not None}
    products = []
    for row in data['searchResult']['results']:
        product = row.get('product', {})
        product_id = str(row.get('id') or product.get('id') or '')
        inventory = inventories.get(product_id, {})
        price = inventory.get('deliveryPrice') or {}
        original_price = inventory.get('originalPrice') or {}
        attributes = product.get('attributes', {})
        rating = product.get('rating') or {}
        images = attributes.get('primary_image', {}).get('text') or []

        def flag(name):
            values = attributes.get(name, {}).get('numbers') or []
            return bool(values[0]) if values else None

        if not product_id or not product.get('title') or not product.get('uri'):
            raise RuntimeError('Product is missing its ID, title or URL.')
        products.append({
            'product_id': product_id,
            'title': product['title'],
            'brands': product.get('brands', []),
            'price_min': price.get('minPrice'),
            'price_max': price.get('maxPrice'),
            'original_price_min': original_price.get('minPrice'),
            'original_price_max': original_price.get('maxPrice'),
            'availability': inventory.get('deliveryAvailability'),
            'member_only': flag('member_only'),
            'buyable': flag('buyable'),
            'rating': rating.get('averageRating'),
            'review_count': rating.get('ratingCount'),
            'promotions': [item['short_text'] for item in inventory.get('promotions', [])
                           if item.get('short_text')],
            'categories': product.get('categories', []),
            'url': product['uri'],
            'image': images[0] if images else None,
        })
    return products


def gzip_bytes(text):
    compressor = zlib.compressobj(wbits=31)
    raw = bytes(json.loads(text))
    result = bytearray(compressor.compress(raw) + compressor.flush())
    result[9] = 10  # 与本机 Chrome CompressionStream 的 gzip OS 字节一致。
    return json.dumps(list(result))


STREAM_PAIR = """(() => {
    const compress = globalThis.__costcoGzip;
    delete globalThis.__costcoGzip;
    let chunks = [], output, sent = false, finish;
    const closed = new Promise(resolve => { finish = resolve; });
    return {
        writable: {getWriter() { return {
            releaseLock() {},
            write(value) {
                const bytes = ArrayBuffer.isView(value)
                    ? new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
                    : new Uint8Array(value);
                chunks.push(...bytes);
                return Promise.resolve();
            },
            close() {
                output = Uint8Array.from(JSON.parse(compress(JSON.stringify(chunks))));
                chunks = [];
                finish();
                return Promise.resolve();
            }
        }; }},
        readable: {getReader() { return {
            releaseLock() {},
            async read() {
                await closed;
                if (sent) return {done: true, value: undefined};
                sent = true;
                return {done: false, value: output};
            }
        }; }}
    };
})()"""


def stream_part(receiver, name):
    # 只提供本次 SDK 实际使用的 gzip 写入、关闭、读取流程。
    if not hasattr(receiver, '_costco_stream_pair'):
        context = pyv8.current_context()
        context.glob['__costcoGzip'] = gzip_bytes
        receiver._costco_stream_pair = context.exec_js(STREAM_PAIR)
    return receiver._costco_stream_pair[name]


def writable(receiver):
    return stream_part(receiver, 'writable')


def readable(receiver):
    return stream_part(receiver, 'readable')


@contextmanager
def compatibility():
    running_state = ChromeBindings.runningState
    index_get = WindowProxy.__v8_index_get__

    def runningState(self, *args):
        if args:
            raise PageTypeError('Error in invocation of app.runningState(): ')
        return running_state(self)

    def window_index(self, index):
        if self._same_origin() and index >= len(self._child_window._frame_windows()):
            return pyv8.Undefined
        return index_get(self, index)

    ChromeBindings.runningState = runningState
    WindowProxy.__v8_index_get__ = window_index
    try:
        yield
    finally:
        ChromeBindings.runningState = running_state
        WindowProxy.__v8_index_get__ = index_get


def main():
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    logger.remove()
    logger.add(sys.stderr, level='INFO', format='{time:HH:mm:ss} | {level} | {message}')
    sdk_root = None
    captured_headers = {}
    stopped = False
    deadline = time.monotonic() + 120

    def intercept(url, request):
        nonlocal sdk_root, captured_headers
        target = urlsplit(url)
        # 留给框架处理本地 URL；False 会制造空的 200 导航，改变 iframe 来源。
        if target.scheme not in ('http', 'https'):
            return None
        if stopped:
            return False
        main_document = target.hostname == 'www.costco.com' and target.path == '/s'
        sdk = sdk_root is not None and target.path.startswith(sdk_root) and target.hostname in (
            'www.costco.com', 'gdx-api.costco.com')
        if not (main_document or sdk or url == API_URL):
            return False
        if time.monotonic() >= deadline:
            raise TimeoutError('Costco request deadline exceeded.')
        method = request.get('method', 'GET').upper()
        if url == API_URL and method == 'POST':
            captured_headers = {key.lower(): value for key, value in request.get('headers', {}).items()
                                if key.lower().startswith('x-kpsdk-')}
        # GET 连接失败可重试；POST 不自动重试。
        for attempt in range(3 if method == 'GET' else 1):
            try:
                response = page.net.request(method, url, headers=request.get('headers'),
                                            data=request.get('body'), timeout=20)
                break
            except Exception:
                if method != 'GET' or attempt == 2:
                    raise
                logger.warning('GET {} 连接失败，重试 {}/2', target.path, attempt + 1)
                time.sleep(1)
        logger.info('{} {} -> {}', method, target.path, response.status)
        body = response.text
        if main_document:
            scripts = [match.group(0) for match in re.finditer(r'<script\b[^>]*>.*?</script>', body, re.I | re.S)
                       if 'KPSDK.configure' in match.group(0)
                       or re.search(r'''src=["'][^"']+/p\.js["']''', match.group(0))]
            if len(scripts) != 2:
                raise RuntimeError('Current HTML does not contain the expected SDK bootstrap.')
            source = re.search(r'''src=["']([^"']+/p\.js)["']''', ''.join(scripts)).group(1)
            sdk_root = urlsplit(source).path.rsplit('/', 1)[0] + '/'
            body = '<!doctype html><html><head><title>Search | Costco</title>' + ''.join(scripts) + '</head><body></body></html>'
        return {'status': response.status, 'headers': response.headers, 'body': body}

    with compatibility():
        browser = Browser(
            proxy=PROXY, cache=False, http_options={'impersonate': 'chrome', 'timeout': 20},
            resource_interceptor=intercept,
            interface_overrides={
                'CompressionStream': {'writable': writable, 'readable': readable},
            },
        )
        page = browser.new_context().new_page()
        try:
            page.goto(PAGE_URL, timeout=75)
            body = json.dumps(search_body(), separators=(',', ':'))
            # 调用页面 fetch，让现场加载的 SDK 自动添加动态头。首个请求可更新服务器令牌。
            for attempt in range(2):
                response = page.net.fetch(API_URL, method='POST', headers=HEADERS, body=body, timeout=50)
                if response.status != 200:
                    raise RuntimeError(f'Search failed: HTTP {response.status}.')
                data = response.json()
                result = data.get('searchResult', {}) if isinstance(data, dict) else {}
                rows = result.get('results')
                if not isinstance(rows, list) or not rows or not any(
                    KEYWORD in str(row.get('product', {}).get('title', '')).lower() for row in rows
                ):
                    raise RuntimeError('Search response does not contain matching product results.')
                if all(captured_headers.get(name) for name in SDK_HEADERS):
                    break
                if attempt == 1:
                    raise RuntimeError('Search returned data but SDK headers are incomplete.')
                logger.info('首个响应已返回数据，继续由 SDK 更新令牌并生成下一次请求。')
            for name in SDK_HEADERS:
                logger.info('{}={}', name, captured_headers[name])
            products = extract_products(data)
            if len(products) != 24 or len({item['product_id'] for item in products}) != 24:
                raise RuntimeError('Expected 24 distinct products on the first search page.')
            logger.info('搜索验收：HTTP {}, 提取 {} 条商品，总数 {}',
                        response.status, len(products), result.get('totalSize'))
            # for index, product in enumerate(products, 1):
            #     logger.info('商品 {:02d}/{}：{}', index, len(products), json.dumps(product, ensure_ascii=False))
            # with open('1.json','w',encoding='utf-8') as f:
            #     f.write(json.dumps(data, ensure_ascii=False, indent=2))
            logger.info(f'搜索接口返回数据状态- {response.status}')
            return 0
        except (Exception, pyv8.JSException) as error:
            logger.error('{}: {}', type(error).__name__,
                         'Native JS execution failed.' if isinstance(error, pyv8.JSException) else str(error))
            return 1
        finally:
            stopped = True
            browser.close()


if __name__ == '__main__':
    raise SystemExit(main())
