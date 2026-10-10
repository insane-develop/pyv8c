import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYV8_ROOT = ROOT.parent / "_pyv8"


def _run(case):
    env = os.environ.copy()
    paths = [str(PYV8_ROOT), str(ROOT)]
    env["PYTHONPATH"] = os.pathsep.join(paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    code = f"""
import faulthandler
faulthandler.enable()
from pyv8c import Browser
print('before-browser', flush=True)
b = Browser()
c = b.new_context()
p = c.new_page()
print('before-content', flush=True)
p.set_content({case!r}, url='http://example.test/')
print('after-content', flush=True)
value = p.evaluate("document.querySelector('#x') && document.querySelector('#x') === document.querySelector('#x')")
print('after-evaluate', value, flush=True)
p.close()
c.close()
b.close()
print('closed', flush=True)
"""
    return subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30,
    )


def test_native_lifecycle_smoke_cases():
    # Two variants cover both parse paths; the point is a crash-free child process.
    for html in ("", "<div id='x' class='a'>text</div>"):
        result = _run(html)
        assert result.returncode == 0, (html, result.returncode, result.stdout, result.stderr)
        assert "closed" in result.stdout, (html, result.stdout, result.stderr)
