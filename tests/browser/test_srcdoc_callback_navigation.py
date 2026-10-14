"""A timer insertion exposes its initial window before navigating srcdoc.

Parsing srcdoc inside the inserting callback can execute frame code too early;
delaying the entire browsing context instead leaves contentWindow null. Both
load and a frame-originated message witness the eventual navigation.
"""
import json
import time

from pyv8c import Browser


def test_srcdoc_inserted_by_timer_has_initial_window_and_later_load():
    inner='<html><body><script>parent.postMessage("srcdoc-ready","*")</script></body></html>'
    quoted=json.dumps(inner).replace('</',r'<\/')
    html='''<!doctype html><body><script>
    window.result={immediate:false,loads:0,messages:0,errors:[]};
    addEventListener('error',e=>result.errors.push(e.message));
    addEventListener('message',e=>{if(e.data==='srcdoc-ready')result.messages++});
    setTimeout(()=>{
      const frame=document.createElement('iframe');
      frame.srcdoc=INNER;
      frame.onload=()=>result.loads++;
      document.body.appendChild(frame);
      result.immediate=!!frame.contentWindow;
    },0);
    </script>'''.replace('INNER',quoted)
    browser=Browser(real_network=False)
    page=browser.new_context().new_page()
    page.realm.executor.default_timeout=3
    try:
        page.set_content(html,url='https://frame.test/')
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=page.evaluate('result')
            if result['loads'] and result['messages']:break
            time.sleep(.02)
        assert result=={'immediate':True,'loads':1,'messages':1,'errors':[]}
    finally:
        browser.close()
