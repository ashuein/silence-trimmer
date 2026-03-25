"""
core/quality.py — Session quality assessment without ground truth.

Strategy:
  We cannot know if a cut was "correct" without human labels. But we CAN
  detect pathological patterns that indicate bad parameter choices:

  1. Trim ratio: <2% → threshold too low (missing silence); >60% → too aggressive
  2. Micro-cuts: speech segments <2s indicate clipping mid-sentence
  3. Cuts/minute: >10 cuts/min → choppy output
  4. Boundary energy: high RMS at cut points → cutting into speech
  5. Silence headroom: how far below threshold is the avg silence energy?
     If close to threshold → many borderline cases, threshold is in the noise floor
"""

import math
import statistics
from typing import Optional

from ..models import (
    TrimConfig, VideoResult, QualityMetrics,
    SilenceInterval,
)


def analyze_quality(
    result: VideoResult,
    config: TrimConfig,
    wav_path: Optional[str] = None,
) -> QualityMetrics:
    """
    Compute quality metrics for a single processed video.
    wav_path: optional pre-extracted WAV for boundary energy analysis.
    """
    q = QualityMetrics()
    dur = result.original_duration

    if dur <= 0:
        q.verdict = "NO_DATA"
        return q

    trimmed_silence_sec = dur - result.trimmed_duration
    q.trim_ratio_pct = round(trimmed_silence_sec / dur * 100, 1)

    # Speech segment stats
    segs = result.speech_segments
    seg_durs = [e - s for s, e in segs]

    if seg_durs:
        q.mean_speech_seg_sec = round(statistics.mean(seg_durs), 2)
        q.median_speech_seg_sec = round(statistics.median(seg_durs), 2)
        q.min_speech_seg_sec = round(min(seg_durs), 2)
        q.micro_cuts = sum(1 for d in seg_durs if d < 2.0)
    else:
        q.verdict = "NO_SPEECH_DETECTED"
        q.recommendations.append("All audio classified as silence. Raise threshold (less negative dB).")
        return q

    # Cuts per minute of original content
    n_cuts = len(segs) - 1
    q.cuts_per_minute = round(n_cuts / (dur / 60), 1) if dur > 0 else 0

    # Boundary energy analysis (if WAV available)
    if wav_path:
        try:
            from .detector import read_wav_rms_at
            boundary_rms = []
            for i, (start, end) in enumerate(segs):
                if i > 0:
                    boundary_rms.append(read_wav_rms_at(wav_path, start, 0.1))
                if i < len(segs) - 1:
                    boundary_rms.append(read_wav_rms_at(wav_path, end, 0.1))
            if boundary_rms:
                q.boundary_energy_db = round(statistics.mean(boundary_rms), 1)
        except Exception:
            pass

    # Silence energy headroom
    above_silences = [s for s in result.silence_intervals if s.above_threshold]
    if above_silences and wav_path:
        try:
            from .detector import read_wav_rms_at
            sil_energies = []
            for sil in above_silences[:20]:  # sample up to 20
                mid = (sil.start + sil.end) / 2
                sil_energies.append(read_wav_rms_at(wav_path, mid, 0.2))
            if sil_energies:
                avg_sil = statistics.mean(sil_energies)
                q.silence_energy_headroom_db = round(
                    config.silence_thresh_db - avg_sil, 1
                )
        except Exception:
            pass

    # --------------- Verdict & Recommendations ---------------
    recs = []

    # Trim ratio
    if q.trim_ratio_pct < 2.0:
        recs.append(
            f"Only {q.trim_ratio_pct}% trimmed. Threshold ({config.silence_thresh_db}dB) "
            f"may be too low. Try -30dB."
        )
    elif q.trim_ratio_pct > 60.0:
        recs.append(
            f"{q.trim_ratio_pct}% trimmed — very aggressive. "
            f"May be removing valid content. Try lowering threshold to -40dB "
            f"or increasing min_silence_duration."
        )

    # Micro-cuts
    if q.micro_cuts > 3:
        recs.append(
            f"{q.micro_cuts} speech segments < 2s (choppy). "
            f"Increase min_silence_duration to {config.min_silence_duration + 0.5:.1f}s "
            f"or increase padding to {config.padding + 0.1:.2f}s."
        )

    # Cuts per minute
    if q.cuts_per_minute > 10:
        recs.append(
            f"{q.cuts_per_minute} cuts/min is high (choppy playback). "
            f"Increase min_silence_duration."
        )

    # Boundary energy
    if q.boundary_energy_db > config.silence_thresh_db + 10:
        recs.append(
            f"High energy at cut boundaries ({q.boundary_energy_db}dB). "
            f"Padding ({config.padding}s) may be too low — try {config.padding + 0.15:.2f}s."
        )

    # Headroom
    if 0 < q.silence_energy_headroom_db < 5:
        recs.append(
            f"Silence energy is only {q.silence_energy_headroom_db}dB below threshold. "
            f"Threshold is near the noise floor — consider background noise reduction "
            f"or switching to silero-vad."
        )

    if not recs:
        q.verdict = "GOOD"
        recs.append("Parameters look well-tuned for this content.")
    elif any("aggressive" in r.lower() or "choppy" in r.lower() for r in recs):
        q.verdict = "NEEDS_ADJUSTMENT"
    else:
        q.verdict = "ACCEPTABLE"

    q.recommendations = recs
    return q
