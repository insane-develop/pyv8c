"""本地生成 Orion Cookie，查询 NORTHWEST；直接运行本文件。"""
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
sys.path[:0] = [str(WORKSPACE / '_pyv8'), str(WORKSPACE / 'pyv8c')]
import pyv8
from loguru import logger
from pyv8c import Browser
from pyv8c.web.dom.document import Document

KEYWORD = 'NORTHWEST'
PROXY = None  # 需要代理时填写 'http://127.0.0.1:7890'
TIMEOUT = 45
ORIGIN = 'https://orion.nv.gov'
URL = ORIGIN + '/portal/public/#/public/nvsos/en/CaseXscreen?screen=external-GenericFilingsSearch&tabRoute=agent'
SDK_PATH = '/sworthe-which-welcolmembling-those-That-th-edgem'
API_URL = ORIGIN + '/core/api/_q/v1/nv-sos/public/CRAEntities'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36'


def interface_overrides(cookie_observer):
    def set_cookie(document, value):
        Document.set_cookie(document, value)
        cookie_observer(document, value)
    return {'Document': {'cookie': {'set': set_cookie}}}


def challenge_document(body):
    scripts = re.findall(r'''<script\b[^>]*\bsrc=["'][^"']*(?:sworthe-which-welcolmembling-those-That-th-edgem|_Incapsula_Resource\?SWJIYLWA=)[^"']*["'][^>]*>\s*</script>''', body, re.I)
    if not scripts:
        raise RuntimeError('No recognized challenge script in the current document.')
    return '<!doctype html><html><head><title>NV-SOS Case Management</title>' + ''.join(scripts) + '</head><body></body></html>'


def search_body():
    params = {'agentName': KEYWORD, 'registeredAgentType': '', 'craId': '', 'nvBusinessId': '',
              'craEntityNumber': '', 'agentStatus': '', 'filingNo': '', 'submit1': False,
              'searchDumy': True, 'restrictedAndProhibitedWords': '', 'totalRecordCount': '',
              'searchType': 'STARTS_WITH', 'searchTerm': ''}
    return {'data': {}, 'origin': '', 'origin_key': 'CaseX',
            'gridInput': {'params': {'parameters': params.copy()}, 'persistPrevParams': False},
            'parameters': params, 'pageNo': 1, 'pageSize': 25, 'sortBy': {}}


def main():
    logger.remove()
    logger.add(sys.stderr, level='INFO', format='{time:HH:mm:ss} | {level} | {message}')
    cookies, posts, documents, stopped = {}, 0, 0, False
    deadline = time.monotonic() + TIMEOUT

    def observe_cookie(document, assignment):
        name, _, value = str(assignment).partition(';')[0].partition('=')
        name = name.strip()
        stored = dict(part.split('=', 1) for part in document.get_cookie().split('; ') if '=' in part)
        if name in ('reese84', '___utmvc') and value and stored.get(name) == value and cookies.get(name) != value:
            cookies[name] = value  # 信标可能删除 ___utmvc，保留它生成时的值。
            logger.info('{}={}', name, value)

    def intercept(url, request):
        nonlocal posts, documents, stopped
        parsed, method = urlsplit(url), request.get('method', 'GET').upper()
        query = parse_qs(parsed.query)
        allowed = parsed.hostname == 'orion.nv.gov' and (
            (parsed.path == '/portal/public/' and method == 'GET') or
            (parsed.path == SDK_PATH and method in ('GET', 'POST')) or
            (parsed.path == '/_Incapsula_Resource' and method == 'GET' and ('SWJIYLWA' in query or 'SWKMTFSR' in query)))
        if stopped or not allowed or time.monotonic() > deadline:
            return False
        posts += method == 'POST'
        documents += parsed.path == '/portal/public/'
        if posts > 3 or documents > 4:
            stopped = True
            return False
        if method == 'POST':
            body = json.loads(request['body'])
            # logger.info(f'生成p值->{body['solution']['interrogation']['p']}')
            if body.get('errors'):
                logger.warning('SDK errors: {}', body['errors'])
        # 宿主 HTTP 使用同一 CookieJar，不递归进入 interceptor。
        response = page.net.request(method, url, headers=request.get('headers'), data=request.get('body'), timeout=20)
        logger.info('{} {} -> {}', method, url, response.status)
        body = response.text
        if parsed.path == '/portal/public/':
            body = challenge_document(body)
        return {'status': response.status, 'statusText': response.status_text, 'body': body, 'headers': response.headers}

    browser = Browser(
        resource_interceptor=intercept, interface_overrides=interface_overrides(cookie_observer=observe_cookie), cache=False,
        http_engine='curl_cffi', http_options={'impersonate': 'chrome150', 'timeout': 20}, proxy=PROXY,
        environment={'user_agent': USER_AGENT, 'languages': ['en-US', 'en'], 'client_hints.full_version_list': [
            {'brand': 'Chromium', 'version': '150.0.0.0'}, {'brand': 'Google Chrome', 'version': '150.0.0.0'},
            {'brand': 'Not A(Brand', 'version': '8.0.0.0'}]})
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(URL)
        while time.monotonic() < deadline and not stopped:
            if any(part.startswith('reese84=') and part[8:] for part in context.cookies.header_for(API_URL).split('; ')):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('Timed out waiting for a fresh reese84 cookie.')
        response = page.net.post(API_URL, json=search_body(), timeout=20)
        logger.info('CRAEntities HTTP {}', response.status)
        try:
            data = response.json()
        except json.JSONDecodeError:
            raise RuntimeError(f'CRAEntities returned non-JSON content: HTTP {response.status}.') from None
        if response.status != 200 or not isinstance(data, dict) or data.get('code') != 0 or not isinstance(data.get('items'), list):
            raise RuntimeError(f'CRAEntities failed: HTTP {response.status}.')
        if any(not str(item.get('agentname', '')).upper().startswith(KEYWORD.upper()) for item in data['items']):
            raise RuntimeError('Returned agent names do not match the keyword.')
        logger.info('CRAEntities HTTP {}, code {}, total {}', response.status, data['code'], data['totalItemCount'])
        logger.info('CRAEntities response:\n{}', json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # 原生 JSException 的字符串可能包含整段 SDK，不展开到日志。
        logger.error('{}: {}', type(exc).__name__, 'Native JS execution failed.' if isinstance(exc, pyv8.JSException) else str(exc))
        return 1
    finally:
        stopped = True
        browser.close()


if __name__ == '__main__':
    raise SystemExit(main())
