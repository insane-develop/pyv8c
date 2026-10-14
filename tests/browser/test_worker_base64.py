"""A worker decodes binary strings, rather than returning an unimplemented undefined."""
import json
import time
from pyv8c import Browser

SOURCE = r'''const errors=[];
for (const fn of [()=>atob('%'),()=>btoa('\u2028'),()=>atob()]) {
    try { fn(); } catch(e) { errors.push(e.name); }
}
postMessage([atob(' YQ =='),btoa('\x00\xff'),atob('/w==').charCodeAt(0),errors]);'''


def test_worker_base64_roundtrip_and_invalid_arguments():
    browser = Browser(real_network=False)
    try:
        page = browser.new_context().new_page()
        page.set_content('<body></body>', url='https://probe.test/')
        page.evaluate('''const w=new Worker(URL.createObjectURL(new Blob([%s],{type:'text/javascript'})));
            w.onmessage=e=>window.result=e.data;''' % json.dumps(SOURCE))
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            result=page.evaluate('window.result')
            if isinstance(result,list):break
            time.sleep(.01)
        assert result == ['a','AP8=',255,['InvalidCharacterError','InvalidCharacterError','TypeError']]
    finally:
        browser.close()
