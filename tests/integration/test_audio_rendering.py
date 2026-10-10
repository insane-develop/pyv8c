"""An offline context renders its graph: the samples an oscillator really produces."""
import math

from pyv8c import Browser

RATE = 44100
LENGTH = 512


def _open():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="https://repro.test/index.html")
    page.run_until_idle(timeout=2)
    return browser, page


def _render(page, body):
    """Run a graph and read what it left in ``window.__rendered``."""
    page.evaluate("window.__rendered = null;")
    page.evaluate("(async () => {" + body + "})()")
    page.run_until_idle(timeout=2)
    return page.evaluate("window.__rendered")


def _graph(node="oscillator.type = 'sine';", gain="gain.gain.value = 0.5;",
           schedule="oscillator.start();"):
    return f"""
        const context = new OfflineAudioContext(1, {LENGTH}, {RATE});
        const oscillator = context.createOscillator();
        oscillator.frequency.value = 440;
        {node}
        const gain = context.createGain();
        {gain}
        oscillator.connect(gain).connect(context.destination);
        {schedule}
        const rendered = await context.startRendering();
        const data = rendered.getChannelData(0);
        window.__rendered = [Array.from(data), rendered.length, rendered.sampleRate,
                             rendered.numberOfChannels, data instanceof Float32Array,
                             context.state, context.currentTime];
    """


def test_a_sine_renders_the_samples_the_frequency_names():
    browser, page = _open()
    try:
        samples, length, rate, channels, typed, state, seconds = _render(page, _graph())
    finally:
        browser.close()

    assert [length, rate, channels, typed, state] == [LENGTH, RATE, 1, True, "closed"]
    assert seconds == LENGTH / RATE
    for index, sample in enumerate(samples):
        wanted = 0.5 * math.sin(2.0 * math.pi * 440 * index / RATE)
        assert abs(sample - wanted) < 1e-5, (index, sample, wanted)


def test_every_waveform_has_the_shape_it_is_named_for():
    #: The RMS Chrome measures for the same waveforms, within the slack its own
    #: per-octave band limiting leaves (its square reads 0.844, its sawtooth 0.487).
    wanted = {"sine": (0.707, 0.01), "square": (0.844, 0.02), "triangle": (0.580, 0.01),
              "sawtooth": (0.490, 0.02)}
    browser, page = _open()
    try:
        for kind, (rms, tolerance) in wanted.items():
            samples = _render(page, _graph(node=f"oscillator.type = '{kind}';",
                                           gain="gain.gain.value = 1;"))[0]
            measured = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
            assert abs(measured - rms) < tolerance, (kind, measured)
            # The table is sampled at 4096 points, so its own peak can pass the
            # continuous one by a hair where the waveform has a sharp corner.
            assert max(abs(sample) for sample in samples) <= 1.001, kind
    finally:
        browser.close()


def test_a_waveform_keeps_the_amplitude_of_the_harmonics_it_has_left():
    """Chrome cuts a table to the harmonics a frequency can carry but scales every
    table by the same factor, so a 10 kHz triangle keeps 8/pi^2 rather than 1."""
    wanted = {("sine", 10000): (1.0, 0.70712), ("square", 10000): (1.07994, 0.76365),
              ("triangle", 10000): (0.81073, 0.57328), ("square", 5000): (1.01818, 0.805),
              ("triangle", 5000): (0.9008, 0.57692)}
    browser, page = _open()
    try:
        for (kind, frequency), (peak, rms) in wanted.items():
            samples = _render(page, f"""
                const context = new OfflineAudioContext(1, 4096, {RATE});
                const oscillator = context.createOscillator();
                oscillator.type = '{kind}';
                oscillator.frequency.value = {frequency};
                oscillator.connect(context.destination);
                oscillator.start(0);
                const data = (await context.startRendering()).getChannelData(0);
                window.__rendered = Array.from(data.slice(1024));
            """)
            measured = max(abs(sample) for sample in samples)
            energy = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
            assert abs(measured - peak) < 1e-3, (kind, frequency, measured, peak)
            assert abs(energy - rms) < 1e-3, (kind, frequency, energy, rms)
    finally:
        browser.close()


def test_a_gain_ramp_scales_the_tone_as_it_climbs():
    browser, page = _open()
    try:
        samples = _render(page, _graph(
            gain=f"gain.gain.setValueAtTime(0, 0);"
                 f" gain.gain.linearRampToValueAtTime(1, {LENGTH / RATE});"))[0]
    finally:
        browser.close()

    for index, sample in enumerate(samples):
        wanted = math.sin(2.0 * math.pi * 440 * index / RATE) * index / LENGTH
        assert abs(sample - wanted) < 1e-5, (index, sample, wanted)


def test_a_source_is_silent_outside_its_start_and_stop():
    half = LENGTH / 2 / RATE
    browser, page = _open()
    try:
        samples = _render(page, _graph(schedule=f"oscillator.start({half});"
                                                f" oscillator.stop({LENGTH / RATE});"))[0]
    finally:
        browser.close()

    assert samples[:LENGTH // 2] == [0.0] * (LENGTH // 2)
    assert any(sample != 0.0 for sample in samples[LENGTH // 2:])


def test_a_buffer_source_plays_the_samples_the_buffer_holds():
    """At rate 1 the buffer comes out as written, then silence, and the end is reported."""
    browser, page = _open()
    try:
        rendered = _render(page, f"""
            const context = new OfflineAudioContext(1, 8, {RATE});
            const buffer = context.createBuffer(1, 4, {RATE});
            buffer.copyToChannel(new Float32Array([0.5, -0.25, 0.125, 1.0]), 0);
            const source = context.createBufferSource();
            source.buffer = buffer;
            let ended = false;
            source.onended = () => {{ ended = true; }};
            source.connect(context.destination);
            source.start();
            const data = (await context.startRendering()).getChannelData(0);
            window.__rendered = [Array.from(data), ended, source.buffer === buffer,
                                 source.playbackRate.value, source.loop];
        """)
    finally:
        browser.close()

    samples, ended, same_buffer, rate, loop = rendered
    assert samples == [0.5, -0.25, 0.125, 1.0, 0.0, 0.0, 0.0, 0.0]
    assert ended is True
    assert [same_buffer, rate, loop] == [True, 1, False]


def test_a_buffer_source_reads_faster_when_its_rate_says_so():
    browser, page = _open()
    try:
        rendered = _render(page, f"""
            const context = new OfflineAudioContext(1, 6, {RATE});
            const buffer = context.createBuffer(1, 4, {RATE});
            buffer.copyToChannel(new Float32Array([0.5, -0.25, 0.125, 1.0]), 0);
            const source = context.createBufferSource();
            source.buffer = buffer;
            source.playbackRate.value = 2;
            source.connect(context.destination);
            source.start();
            window.__rendered = Array.from(
                (await context.startRendering()).getChannelData(0));
        """)
    finally:
        browser.close()

    assert rendered == [0.5, 0.125, 0.0, 0.0, 0.0, 0.0]


def test_a_compressor_takes_a_full_scale_tone_down():
    """A full-scale sine through the defaults lands at -22 dB, which reduction reports."""
    browser, page = _open()
    try:
        reduction, peak = _render(page, f"""
            const context = new OfflineAudioContext(1, 4096, {RATE});
            const oscillator = context.createOscillator();
            const compressor = context.createDynamicsCompressor();
            oscillator.connect(compressor).connect(context.destination);
            oscillator.start();
            const data = (await context.startRendering()).getChannelData(0);
            let peak = 0;
            for (let i = 2048; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i]));
            window.__rendered = [compressor.reduction, peak];
        """)
    finally:
        browser.close()

    assert -22.5 < reduction < -21.5, reduction
    assert abs(peak - 10 ** (-22 / 20)) < 0.01, peak


def test_a_buffer_hands_back_a_float32_array_that_holds_what_was_written():
    browser, page = _open()
    try:
        result = _render(page, f"""
            const context = new OfflineAudioContext(1, 8, {RATE});
            const buffer = context.createBuffer(1, 8, {RATE});
            buffer.copyToChannel(new Float32Array([0.5, -0.25, 0.125]), 0, 2);
            const data = buffer.getChannelData(0);
            window.__rendered = [data instanceof Float32Array, data === buffer.getChannelData(0),
                                 Array.from(data)];
        """)
    finally:
        browser.close()

    assert result[0] is True
    assert result[1] is True
    assert result[2] == [0.0, 0.0, 0.5, -0.25, 0.125, 0.0, 0.0, 0.0]


def test_a_waveform_scale_is_the_peak_of_its_series():
    """Each scale is a constant of its waveform, carried as one: the search it came
    from has to agree with it, digit for digit."""
    from pyv8c.web.bom.audio import _WAVE_POINTS, _wave_samples, _wave_scale

    for kind in ("sine", "square", "sawtooth", "triangle"):
        peak = max(abs(value) for value in _wave_samples(kind, _WAVE_POINTS // 2)) or 1.0
        assert _wave_scale(kind) == 1.0 / peak, kind
