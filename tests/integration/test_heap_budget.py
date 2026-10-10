"""A page that outgrows the isolate's heap budget is stopped, and the host lives."""
import pytest
import subprocess
import sys
import textwrap

#: The budget is a process-wide V8 setting, so this runs in a process of its own.
SCRIPT = """
import pyv8

pyv8.configure(params=["--max-old-space-size=256"])

from pyv8c import Browser
from pyv8c.web.fetch.response import Response

HTML = ("<body><script>var a = [];"
        " for (let i = 0; i < 200000000; i++) a.push(i);</script></body>")


def loader(url, request):
    return Response(HTML, {"url": url, "content-type": "text/html"})


browser = Browser(resource_loader=loader, stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.set_content(HTML, url="http://example.test/")
    print("readyState", page.evaluate("document.readyState"))
    print("terminations", page.context.heap_terminations)
finally:
    browser.close()
print("closed")
"""


@pytest.mark.slow  # a V8 heap limit takes the process down, not just the test
def test_a_page_that_outgrows_the_heap_is_stopped_and_the_host_survives():
    """The runaway script ends as a terminated crossing: the document reaches an end,
    the stop is reported, and the host closes on its own terms."""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(SCRIPT)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    tokens = result.stdout.split()
    assert tokens[0] == "readyState" and tokens[1] in ("loading", "interactive", "complete")
    assert tokens[2] == "terminations" and int(tokens[3]) >= 1
    assert tokens[4:] == ["closed"]
