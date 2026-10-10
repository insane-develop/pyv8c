"""``OffscreenCanvas`` hands out a working 2D surface with no element behind it."""
import pytest

from pyv8c import Browser


def test_an_offscreen_canvas_draws_and_reports_its_pixels():
    pytest.importorskip("PIL", reason="Pillow backs the canvas drawing surface")
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://repro.test/index.html")
        page.run_until_idle(timeout=2)
        value = page.evaluate("""(() => {
            const canvas = new OffscreenCanvas(8, 6);
            const context = canvas.getContext('2d');
            context.fillStyle = '#ff0000';
            context.fillRect(0, 0, 4, 3);
            return [OffscreenCanvas.length, canvas.width, canvas.height,
                    canvas.constructor.name, canvas.getContext('2d') === context,
                    context instanceof OffscreenCanvasRenderingContext2D,
                    context.canvas === canvas, canvas.getContext('nope') === null,
                    typeof canvas.convertToBlob, typeof canvas.transferToImageBitmap,
                    typeof canvas.oncontextlost,
                    Array.from(context.getImageData(0, 0, 1, 1).data)];
        })()""")
    finally:
        browser.close()

    assert value == [2, 8, 6, "OffscreenCanvas", True, True, True, True, "function",
                     "function", "object", [255, 0, 0, 255]]
