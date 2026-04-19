"""Silence trimming — compress long silence regions to a maximum of 2 seconds.

Operates on 16-bit mono PCM.  Detects truly silent segments (flat waveform)
using peak amplitude, bridges micro-bursts of noise, and truncates any
continuous silence exceeding MAX_SILENCE_SEC.
"""
import numpy as np

from core.log import logger

_TAG = "[Trim]"

MAX_SILENCE_SEC = 2.0
WINDOW_MS = 20
PEAK_THRESHOLD = 80       # int16 peak; ~0.24% of full scale
BRIDGE_MS = 100           # ignore non-silent blips shorter than this


def trim_silence(pcm: bytes, sample_rate: int = 16000) -> tuple[bytes, dict]:
    """Remove excessive silence from PCM, keeping at most 2s per gap.

    Returns (trimmed_pcm, trim_info_dict).
    """
    samples = np.frombuffer(pcm, dtype=np.int16)
    total_samples = len(samples)
    if total_samples == 0:
        return pcm, _empty_info(0.0)

    original_dur = total_samples / sample_rate

    win_samples = int(sample_rate * WINDOW_MS / 1000)
    if win_samples < 1:
        win_samples = 1
    n_windows = total_samples // win_samples

    if n_windows == 0:
        return pcm, _empty_info(original_dur)

    logger.info(f"{_TAG} Analyzing {original_dur:.1f}s audio "
                f"(peak_threshold={PEAK_THRESHOLD}, bridge={BRIDGE_MS}ms)")

    usable = n_windows * win_samples
    matrix = np.abs(samples[:usable].reshape(n_windows, win_samples))
    peaks = matrix.max(axis=1)

    is_silent = peaks < PEAK_THRESHOLD

    bridge_windows = max(1, int(BRIDGE_MS / WINDOW_MS))
    is_silent = _bridge_micro_bursts(is_silent, bridge_windows)

    regions = _find_silence_regions(is_silent, win_samples)

    max_silence_samples = int(MAX_SILENCE_SEC * sample_rate)
    trim_details = []
    kept_ranges: list[tuple[int, int]] = []
    prev_end = 0

    for reg_start, reg_end in regions:
        reg_len = reg_end - reg_start
        if reg_len <= max_silence_samples:
            continue

        kept_ranges.append((prev_end, reg_start + max_silence_samples))

        removed_sec = (reg_len - max_silence_samples) / sample_rate
        trim_details.append({
            "at_sec": round(reg_start / sample_rate, 1),
            "original_sec": round(reg_len / sample_rate, 1),
            "kept_sec": MAX_SILENCE_SEC,
        })
        logger.info(f"{_TAG} Region at {reg_start / sample_rate:.1f}s: "
                    f"{reg_len / sample_rate:.1f}s → kept {MAX_SILENCE_SEC}s "
                    f"(removed {removed_sec:.1f}s)")
        prev_end = reg_end

    if not trim_details:
        logger.info(f"{_TAG} No silence regions > {MAX_SILENCE_SEC}s found, "
                    f"audio unchanged ({original_dur:.1f}s)")
        return pcm, _empty_info(original_dur)

    kept_ranges.append((prev_end, total_samples))

    parts = [samples[s:e] for s, e in kept_ranges if s < e]
    trimmed = np.concatenate(parts) if parts else samples
    trimmed_pcm = trimmed.astype(np.int16).tobytes()

    trimmed_dur = len(trimmed) / sample_rate
    saved_sec = original_dur - trimmed_dur

    logger.info(f"{_TAG} Result: {original_dur:.1f}s → {trimmed_dur:.1f}s "
                f"(saved {saved_sec:.1f}s, {len(trim_details)} regions trimmed)")

    return trimmed_pcm, {
        "original_duration_sec": round(original_dur, 1),
        "trimmed_duration_sec": round(trimmed_dur, 1),
        "saved_sec": round(saved_sec, 1),
        "regions_trimmed": len(trim_details),
        "details": trim_details,
    }


def _bridge_micro_bursts(is_silent: np.ndarray, bridge_windows: int) -> np.ndarray:
    """Reclassify short non-silent gaps between silent regions as silent."""
    result = is_silent.copy()
    n = len(result)
    i = 0
    while i < n:
        if not result[i]:
            j = i
            while j < n and not result[j]:
                j += 1
            gap_len = j - i
            if gap_len <= bridge_windows and i > 0 and j < n:
                result[i:j] = True
            i = j
        else:
            i += 1
    return result


def _find_silence_regions(
    is_silent: np.ndarray, win_samples: int
) -> list[tuple[int, int]]:
    """Convert per-window silent flags to sample-level regions."""
    regions: list[tuple[int, int]] = []
    n = len(is_silent)
    i = 0
    while i < n:
        if is_silent[i]:
            j = i
            while j < n and is_silent[j]:
                j += 1
            regions.append((i * win_samples, j * win_samples))
            i = j
        else:
            i += 1
    return regions


def _empty_info(original_dur: float) -> dict:
    return {
        "original_duration_sec": round(original_dur, 1),
        "trimmed_duration_sec": round(original_dur, 1),
        "saved_sec": 0.0,
        "regions_trimmed": 0,
        "details": [],
    }
