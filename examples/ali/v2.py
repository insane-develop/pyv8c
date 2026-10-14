"""本地初始化阿里云滑块、拖动、点击按钮，再提交短信验证码接口。"""
import json
from pathlib import Path
import random
import sys
import time
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
sys.path[:0] = [str(WORKSPACE / '_pyv8'), str(WORKSPACE / 'pyv8c')]
import pyv8
from loguru import logger
from pyv8c import Browser
from pyv8c.platform import input as mouse_input
from pyv8c.web.bom.window import Window
from pyv8c.web.dom import hit_testing, layout
from pyv8c.web.dom.html_elements import HTMLElement
from pyv8c.web.dom.html_iframe import HTMLIFrameElement

PHONES = [f'134037{i}365' for i in range(10,100)]
PROXY = None
TIMEOUT = 60
SLIDE_WIDTH, SLIDE_HEIGHT = 342, 31
URL = 'https://console.beeize.com/login'
API_URL = 'https://console.beeize.com/api/verify/code/captcha'
BOOTSTRAP = 'https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36'
CONFIG = {'SceneId':'1almkyfs','prefix':'19vnrl','mode':'embed','element':'#captcha-element',
          'button':'#captcha-button','slideStyle':{'width':SLIDE_WIDTH,'height':SLIDE_HEIGHT},'language':'cn'}
HTML = '''<!doctype html><html><head><meta charset="UTF-8"><title>Login</title>
<style>*{box-sizing:border-box}html,body{margin:0;width:1904px;height:929px}#captcha-element{width:342px;margin:80px 40px}
#captcha-button{width:160px;height:40px;margin:20px 40px}</style></head><body>
<div id="captcha-element"></div><button id="captcha-button">获取验证码</button>
<script src="''' + BOOTSTRAP + '''"></script><script>
globalThis.__errors=[];globalThis.__captchaParam=null;
window.addEventListener('error',e=>__errors.push(e.message));
window.addEventListener('unhandledrejection',e=>__errors.push(String(e.reason)));
initAliyunCaptcha(Object.assign(''' + json.dumps(CONFIG) + ''',{
 captchaVerifyCallback:value=>{globalThis.__captchaParam=value;},getInstance:value=>{globalThis.__captcha=value;}
}));</script></body></html>'''


def get_phone():
    return random.choice(PHONES)
def content_window(frame):
    child=frame._child_services
    parent=Window._accessor_window(frame._services.document_services.window)
    if child is not None and child._origin==parent._origin:
        return child._page_realm.context.js_obj(child.window)
    return HTMLIFrameElement.get_contentWindow(frame)


def font_span_size(element,axis):
    # 字体探测的绝对定位叶节点单独测量，避免每次读取都重排整棵 DOM。
    inline=element.attributes.get('style','')
    if (element.localName=='span' and element.isConnected and 'font-family:' in inline
            and 'position: absolute' in inline and not any(node.nodeType==1 for node in element._children)):
        style=layout.computed_style(element)
        value=layout.text_width(element.get_textContent(),style) if axis=='width' else layout._line_height(style)
        return int(round(value))
    return HTMLElement.get_offsetWidth(element) if axis=='width' else HTMLElement.get_offsetHeight(element)


def offset_width(element):return font_span_size(element,'width')
def offset_height(element):return font_span_size(element,'height')


def slider_point(document,x,y):
    # 框架按 DOM 次序命中；此组件 slider(z=2) 应位于 text-box(z=1) 上方。
    thumb=document.fn_getElementById('aliyunCaptcha-sliding-slider')
    if thumb is not pyv8.Null:
        box=hit_testing.border_box(thumb)
        if box.left<=x<box.left+box.width and box.top<=y<box.top+box.height:
            return thumb
    return _original_point(document,x,y)


_original_point=hit_testing.element_from_point


def rectangles(page):
    return page.evaluate("['aliyunCaptcha-sliding-body','aliyunCaptcha-sliding-slider','captcha-button'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})")


def bezier_track(body,thumb,rng):
    x,y=thumb['x']+thumb['width']/2,thumb['y']+min(thumb['height'],body['height'])/2
    end=body['x']+body['width']-thumb['width']/2+1
    if min(body['height'],thumb['width'],thumb['height'])<=0 or end<=x:
        raise RuntimeError('Invalid slider geometry.')
    # 在单位坐标中构造三阶贝塞尔曲线，再分别按实际行程与轨道高度缩放。
    p1=(rng.uniform(0.12,0.28),rng.uniform(-0.10,0.10))
    p2=(rng.uniform(0.70,0.88),rng.uniform(-0.10,0.10))
    samples=rng.randint(36,54)
    duration=rng.uniform(1.45,2.15)
    intervals=[rng.uniform(0.75,1.25) for _ in range(samples)]
    total=sum(intervals)
    elapsed=0
    points=[(x,y,0)]
    for index,interval in enumerate(intervals,1):
        t=index/samples
        u=1-t
        bx=3*u*u*t*p1[0]+3*u*t*t*p2[0]+t*t*t
        by=3*u*u*t*p1[1]+3*u*t*t*p2[1]
        elapsed+=interval
        points.append((x+(end-x)*bx,y+body['height']*by,duration*elapsed/total))
    return points


def drag(page,body,thumb):
    rng=random.Random()
    points=bezier_track(body,thumb,rng)
    x,y,_=points[0]
    page.mouse.move(x,y)
    time.sleep(rng.uniform(0.14,0.24))
    page.mouse.down()
    time.sleep(rng.uniform(0.07,0.12))
    started=time.monotonic()
    # 按绝对时刻采样，避免每次 DOM 派发耗时再叠加 sleep，拖长整段轨迹。
    for x,y,at in points[1:]:
        time.sleep(max(0,started+at-time.monotonic()))
        page.mouse.move(x,y)
    page.mouse.up()
    text=page.evaluate("document.getElementById('aliyunCaptcha-sliding-text-box').textContent")
    logger.info('Bezier drag: {} points, duration {:.2f}s; {}',len(points)-1,time.monotonic()-started,text.strip())
    if '滑动完成' not in text:
        raise RuntimeError('SDK did not complete the drag; SMS request was not sent.')


def main():
    if hasattr(sys.stderr,'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    logger.remove()
    logger.add(sys.stderr,level='INFO',format='{time:HH:mm:ss} | {level} | {message}')
    actions=[]
    stopped=False
    deadline=time.monotonic()+TIMEOUT
    original_mouse_point=mouse_input.element_from_point
    hit_testing.element_from_point=slider_point
    mouse_input.element_from_point=slider_point
    page=None

    def intercept(url,request):
        parts=urlsplit(url)
        if stopped or time.monotonic()>deadline:return False
        if url==URL:return {'body':HTML,'headers':{'content-type':'text/html'}}
        if url=='about:blank':return {'body':'<!doctype html><html><head></head><body></body></html>','headers':{'content-type':'text/html'}}
        allowed=parts.hostname in {'19vnrl.captcha-open.aliyuncs.com','upload.captcha-open.aliyuncs.com',
                                  'cloudauth-device-dualstack.cn-shanghai.aliyuncs.com'} or (
            parts.hostname in {'o.alicdn.com','g.alicdn.com'} and parts.path.startswith('/captcha-frontend/'))
        if not allowed:return False
        method=request.get('method','GET')
        response=page.net.request(method,url,headers=request.get('headers'),data=request.get('body'),timeout=20)
        form=parse_qs(request.get('body') or '') if method=='POST' else {}
        action=form.get('Action',[''])[0]
        if action:actions.append(action)
        logger.info('{} {} {} -> {}',method,action,url,response.status)
        text=response.text
        if parts.path.endswith('/AliyunCaptcha.js'):
            # 框架异步创建 iframe，等待其 contentWindow 可用再检查 readyState。
            needle='var u=function(){n||("complete"===i.contentWindow.document.readyState?o():K()(u,10))};u()'
            if text.count(needle)!=1:raise RuntimeError('AliyunCaptcha bootstrap changed; recheck iframe compatibility.')
            text=text.replace(needle,'var u=function(){if(!i.contentWindow)return K()(u,10);n||("complete"===i.contentWindow.document.readyState?o():K()(u,10))};u()')
        return {'status':response.status,'body':text,'headers':response.headers}

    browser=Browser(resource_interceptor=intercept,cache=False,proxy=PROXY,
                    http_options={'impersonate':'chrome150','timeout':20},
                    interface_overrides={'HTMLIFrameElement':{'contentWindow':content_window},
                                         'HTMLElement':{'offsetWidth':offset_width,'offsetHeight':offset_height}},
                    environment={'user_agent':USER_AGENT,'languages':['zh-CN','zh']})
    page=browser.new_context().new_page()
    try:
        PHONE=get_phone()
        page.goto(URL)
        while time.monotonic()<deadline:
            state=page.evaluate("({ready:!!document.getElementById('aliyunCaptcha-sliding-slider'),errors:__errors})")
            if state['errors']:raise RuntimeError(str(state['errors']))
            if state['ready'] and 'Log2' in actions:break
            time.sleep(0.2)
        else:raise TimeoutError('SDK initialization timed out.')
        # 当前布局引擎不解析 CSS var()；显式使用同一初始化配置的尺寸。
        page.evaluate(f"(()=>{{const body=document.getElementById('aliyunCaptcha-sliding-body'),thumb=document.getElementById('aliyunCaptcha-sliding-slider');body.style.width='{SLIDE_WIDTH}px';body.style.height='{SLIDE_HEIGHT}px';thumb.style.width='{SLIDE_HEIGHT}px';thumb.style.height='{SLIDE_HEIGHT}px';thumb.style.lineHeight='{SLIDE_HEIGHT}px';}})()")
        body,thumb,button=rectangles(page)
        drag(page,body,thumb)
        time.sleep(0.4)
        page.mouse.click(button['x']+button['width']/2,button['y']+button['height']/2,delay=0.07)
        while time.monotonic()<deadline:
            param=page.evaluate('globalThis.__captchaParam')
            if isinstance(param,str) and param:break
            time.sleep(0.2)
        else:raise TimeoutError('Click did not produce captchaRequestParam.')
        payload=json.loads(param)
        if payload.get('sceneId')!=CONFIG['SceneId'] or not all(payload.get(k) for k in ('certifyId','deviceToken','data')):
            raise RuntimeError('Incomplete captchaRequestParam.')
        logger.info('Payload generated locally: certifyId={}, deviceToken={} chars, data={} chars',
                    payload['certifyId'],len(payload['deviceToken']),len(payload['data']))
        response=page.net.post(API_URL,headers={'Accept':'application/json, text/plain, */*'},
                               json={'codeType':'PHONE','contact':PHONE,'captchaRequestParam':param},timeout=20)
        data=response.json()
        logger.info('发送手机号，{} Business HTTP {}: {}',PHONE,response.status,json.dumps(data,ensure_ascii=False))
        if response.status!=200 or data.get('code')!=0 or data.get('data')!='验证码发送成功':
            raise RuntimeError('The live API did not confirm SMS success; no automatic retry.')
        return 0
    except Exception as exc:
        logger.error('{}: {}',type(exc).__name__,'Native JS error.' if isinstance(exc,pyv8.JSException) else str(exc))
        return 1
    finally:
        stopped=True
        browser.close()
        hit_testing.element_from_point=_original_point
        mouse_input.element_from_point=original_mouse_point


if __name__=='__main__':
    raise SystemExit(main())
