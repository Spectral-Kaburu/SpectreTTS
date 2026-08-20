"""
SpectreTTS - Latency Profiler
--------------------------------
Measures exactly where time goes between calling .speak() and the
first audio chunk actually reaching the playback queue, so we know
whether to optimize model load, phonemization, or first-chunk synthesis.

Run directly:
    python engine/profile_latency.py

First run will include one-time model loading time (expected to be
slow). Run it TWICE in the same process — second call shows the real
warm-state latency, which is what you experience on every hotkey press
after the daemon has been running a while.
"""

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tts_engine import TTSEngine


def timed_speak(engine: TTSEngine, text: str, label: str):
    print(f"\n--- {label} ---")
    t_start = time.perf_counter()

    # Patch the queue's put to timestamp the first item
    original_put = engine._audio_queue.put
    first_audio_time = {"t": None}

    def timestamped_put(item):
        if first_audio_time["t"] is None and item is not None:
            first_audio_time["t"] = time.perf_counter()
        return original_put(item)

    engine._audio_queue.put = timestamped_put

    engine.speak(text)

    # Wait until first audio lands (or timeout)
    timeout = time.perf_counter() + 15
    while first_audio_time["t"] is None and time.perf_counter() < timeout:
        time.sleep(0.01)

    engine._audio_queue.put = original_put   # restore

    if first_audio_time["t"] is None:
        print("  TIMEOUT — no audio reached the queue within 15s")
        return

    latency = first_audio_time["t"] - t_start
    print(f"  Time to first audio chunk: {latency:.3f}s")

    # Let it finish before the next test
    while engine.is_speaking:
        time.sleep(0.1)


if __name__ == "__main__":
    engine = TTSEngine()

    short_text = "Hello there."

    timed_speak(engine, short_text, "COLD (includes model load)")
    timed_speak(engine, short_text, "WARM (model already loaded — this is the real hotkey-press experience)")
    timed_speak(engine, short_text, "WARM run 2 (confirm consistency)")
    