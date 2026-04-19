"""Chunked ASR — split long audio at relative-silence points and transcribe concurrently.

Uses a greedy algorithm that maximises chunk length (up to MAX_CHUNK_SEC)
while preferring to cut at the quietest points in each search window.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from core.asr import DashScopeASR
from core.log import logger

_TAG = "[Chunk]"

MAX_CHUNK_SEC = 240       # 4 min — safe API limit (5 min / 10 MB)
MIN_CHUNK_SEC = 120       # 2 min — avoid overly short leading chunks
WINDOW_MS = 50            # energy analysis window
SMOOTH_MS = 300           # moving-average smoothing window
QUIET_PERCENTILE = 10     # pick split from quietest 10% of windows


def transcribe_chunked(
    asr: DashScopeASR,
    pcm: bytes,
    sample_rate: int = 16000,
) -> tuple[str, dict]:
    """Split PCM at silence boundaries and transcribe chunks concurrently.

    Returns (concatenated_text, chunk_info_dict).
    """
    bytes_per_sec = sample_rate * 2  # 16-bit mono
    total_dur = len(pcm) / bytes_per_sec

    logger.info(f"{_TAG} Analyzing {total_dur:.1f}s audio for split points "
                f"(max_chunk={MAX_CHUNK_SEC}s)")

    splits = find_silence_splits(pcm, sample_rate)
    chunks = _split_pcm(pcm, splits)

    durations = [len(c) / bytes_per_sec for c in chunks]
    logger.info(f"{_TAG} Result: {len(chunks)} chunks "
                f"[{', '.join(f'{d:.0f}s' for d in durations)}]")

    logger.info(f"{_TAG} Transcribing {len(chunks)} chunks concurrently...")
    wall_t0 = time.perf_counter()

    chunk_results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futures = {}
        for i, chunk_pcm in enumerate(chunks):
            fut = pool.submit(_transcribe_one, asr, chunk_pcm, sample_rate, i)
            futures[fut] = i

        for fut in as_completed(futures):
            idx = futures[fut]
            result = fut.result()  # raises on error
            chunk_results[idx] = result
            logger.info(f"{_TAG} Chunk {idx + 1}/{len(chunks)} done in "
                        f"{result['asr_time_sec']:.1f}s → {result['char_count']} chars")

    wall_sec = time.perf_counter() - wall_t0
    total_api_sec = sum(r["asr_time_sec"] for r in chunk_results.values())

    texts = [chunk_results[i]["text"] for i in range(len(chunks))]
    full_text = "".join(texts)

    logger.info(f"{_TAG} All chunks done in {wall_sec:.1f}s wall / "
                f"{total_api_sec:.1f}s total API")

    split_offsets = [0] + splits + [len(pcm)]
    chunk_info = {
        "total_chunks": len(chunks),
        "algorithm": "relative_silence_greedy",
        "chunks": [],
        "total_asr_time_sec": round(total_api_sec, 1),
        "wall_time_sec": round(wall_sec, 1),
    }
    for i in range(len(chunks)):
        start_byte = split_offsets[i]
        end_byte = split_offsets[i + 1]
        r = chunk_results[i]
        reason = "final_segment" if i == len(chunks) - 1 else "silence"
        chunk_info["chunks"].append({
            "index": i,
            "start_sec": round(start_byte / bytes_per_sec, 1),
            "end_sec": round(end_byte / bytes_per_sec, 1),
            "duration_sec": round((end_byte - start_byte) / bytes_per_sec, 1),
            "pcm_bytes": end_byte - start_byte,
            "split_reason": reason,
            "asr_time_sec": r["asr_time_sec"],
            "char_count": r["char_count"],
        })

    return full_text, chunk_info


def find_silence_splits(pcm: bytes, sample_rate: int = 16000) -> list[int]:
    """Find optimal byte offsets to split PCM using relative-silence greedy."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    total_samples = len(samples)
    bytes_per_sec = sample_rate * 2
    bytes_per_sample = 2

    win_samples = int(sample_rate * WINDOW_MS / 1000)
    n_windows = total_samples // win_samples
    if n_windows == 0:
        return []

    usable = n_windows * win_samples
    matrix = samples[:usable].reshape(n_windows, win_samples)
    rms = np.sqrt(np.mean(matrix ** 2, axis=1))

    smooth_win = max(1, int(SMOOTH_MS / WINDOW_MS))
    if smooth_win > 1 and n_windows >= smooth_win:
        kernel = np.ones(smooth_win) / smooth_win
        smoothed = np.convolve(rms, kernel, mode="same")
    else:
        smoothed = rms

    logger.info(f"{_TAG} Energy curve: {n_windows} windows, "
                f"smoothed {SMOOTH_MS}ms, "
                f"range {smoothed.min():.0f}-{smoothed.max():.0f} RMS")

    max_chunk_bytes = MAX_CHUNK_SEC * bytes_per_sec
    min_chunk_bytes = MIN_CHUNK_SEC * bytes_per_sec
    total_bytes = len(pcm)

    splits: list[int] = []
    pos = 0

    while pos + max_chunk_bytes < total_bytes:
        search_start_byte = pos + min_chunk_bytes
        search_end_byte = pos + max_chunk_bytes

        ss_win = int(search_start_byte / bytes_per_sample) // win_samples
        se_win = min(int(search_end_byte / bytes_per_sample) // win_samples, n_windows)

        if ss_win >= se_win:
            split_at = min(pos + max_chunk_bytes, total_bytes)
            split_at = (split_at // bytes_per_sample) * bytes_per_sample
            splits.append(split_at)
            pos = split_at
            continue

        window_energies = smoothed[ss_win:se_win]
        n_candidates = max(1, int(len(window_energies) * QUIET_PERCENTILE / 100))

        sorted_indices = np.argsort(window_energies)
        quietest = sorted_indices[:n_candidates]

        best_win_local = quietest[np.argmax(quietest)]
        best_win_global = ss_win + best_win_local
        best_sample = best_win_global * win_samples
        split_at = best_sample * bytes_per_sample

        pct = (np.searchsorted(np.sort(smoothed), smoothed[best_win_global]) /
               n_windows * 100)
        logger.info(f"{_TAG} Split at {split_at / bytes_per_sec:.1f}s "
                    f"(energy percentile {pct:.0f}%, "
                    f"smooth RMS={smoothed[best_win_global]:.0f})")

        splits.append(split_at)
        pos = split_at

    return splits


def _split_pcm(pcm: bytes, splits: list[int]) -> list[bytes]:
    """Split PCM bytes at the given offsets."""
    boundaries = [0] + splits + [len(pcm)]
    return [pcm[boundaries[i]:boundaries[i + 1]]
            for i in range(len(boundaries) - 1)
            if boundaries[i] < boundaries[i + 1]]


def _transcribe_one(
    asr: DashScopeASR, chunk_pcm: bytes, sample_rate: int, index: int
) -> dict:
    """Transcribe a single chunk. Called from thread pool."""
    t0 = time.perf_counter()
    text = asr.transcribe(chunk_pcm, sample_rate=sample_rate)
    elapsed = time.perf_counter() - t0
    return {
        "text": text,
        "asr_time_sec": round(elapsed, 1),
        "char_count": len(text),
    }
