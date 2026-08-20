"""
SpectreTTS - Word Timing Estimator
-------------------------------------
Kokoro-onnx's create_stream() hands back raw audio samples with no
word-boundary metadata, so there's no ground-truth signal to say
"word N started playing at sample M". This module fakes it well
enough for a karaoke-style highlight: it estimates, on a pure TIME
basis, how long each word probably takes to say, and lets the
playback thread schedule highlight events off that estimate instead
of off real STT/alignment data.

The estimate is two-stage:
  1. A words-per-second rate, derived from a typical speaking pace
     and scaled by the engine's current playback speed multiplier.
  2. Within a chunk, that chunk's total estimated duration is split
     across its words proportionally to word length (longer words
     get more time), not split evenly — "SpectreTTS" and "a" should
     not get the same highlight duration.

This is a heuristic, not a transcript alignment. It will drift on
long chunks and it doesn't know about pauses Kokoro inserts for
punctuation. Good enough for "which word is roughly being read right
now", not good enough for anything that needs frame accuracy.
"""

import re

# Baseline speaking rate at 1.0x engine speed. ~155 wpm is a normal
# conversational pace for TTS narration (a bit slower than fast human
# speech, a bit faster than a deliberate reading).
BASE_WORDS_PER_SECOND = 155 / 60.0

# Every word gets at least this many "weight units" of its own, on top
# of its character count, so short words (a, is, to) still get a
# perceptible highlight instead of flashing for a few milliseconds.
MIN_WORD_WEIGHT = 3.0


def extract_words(text: str) -> list[re.Match]:
    """
    Returns every whitespace-delimited token in `text` as a regex Match,
    so callers get both the word string (.group()) and its exact char
    span (.start(), .end()) in the ORIGINAL string. Spans are what the
    reader window uses to place highlight tags, so they must point into
    the same string the reader is displaying.
    """
    return list(re.finditer(r"\S+", text))


def estimate_chunk_duration(word_count: int, speed: float) -> float:
    """Rough total seconds a chunk of `word_count` words will take to speak."""
    if word_count <= 0:
        return 0.0
    rate = BASE_WORDS_PER_SECOND * max(0.25, speed)   # guard against speed=0
    return word_count / rate


def distribute_word_durations(words: list[str], total_duration: float) -> list[float]:
    """
    Splits `total_duration` seconds across `words` proportionally to
    word length, so "SpectreTTS" lingers longer than "a". Returns a
    list of per-word durations in seconds, same length/order as `words`.
    """
    if not words:
        return []
    weights = [len(w) + MIN_WORD_WEIGHT for w in words]
    total_weight = sum(weights)
    return [total_duration * (w / total_weight) for w in weights]


def build_word_schedule(word_matches: list[re.Match], speed: float) -> list[tuple[float, int, int]]:
    """
    Given the word Match objects for one chunk, returns a list of
    (delay_seconds, char_start, char_end) tuples — how long to wait
    from the moment this chunk starts playing before highlighting each
    word, and where that word sits in the source text.
    """
    if not word_matches:
        return []
    words = [m.group() for m in word_matches]
    total_duration = estimate_chunk_duration(len(words), speed)
    durations = distribute_word_durations(words, total_duration)

    schedule = []
    cumulative = 0.0
    for match, duration in zip(word_matches, durations):
        schedule.append((cumulative, match.start(), match.end()))
        cumulative += duration
    return schedule
