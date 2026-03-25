"""
core/tagger.py — Topic segmentation: transcribe audio, chunk, extract topics.

Pipeline:
  1. Transcribe via faster-whisper or openai-whisper (word-level timestamps)
  2. Chunk transcript into fixed-duration windows (e.g., 60s)
  3. Extract topic labels per chunk via TF-IDF keyword extraction
  4. Optionally refine via LLM (Anthropic API) if configured

Dependencies (optional — feature degrades gracefully):
  pip install faster-whisper   (preferred, uses CTranslate2)
  OR
  pip install openai-whisper   (original, slower)
  pip install scikit-learn     (for TF-IDF)
"""

import math
import os
import re
import tempfile
from typing import Callable, Optional

from ..models import TrimConfig, TopicSegment


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_audio(
    filepath: str,
    config: TrimConfig,
    model_size: str = "base",
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Transcribe video/audio → list of {start, end, text} segments.
    Tries faster-whisper first, falls back to openai-whisper.
    """
    # Extract audio to temp WAV
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        from .detector import extract_audio_wav
        extract_audio_wav(
            filepath,
            wav_path,
            sr=16000,
            ffmpeg_threads=config.ffmpeg_threads,
        )

        try:
            return _transcribe_faster_whisper(wav_path, model_size, progress_cb)
        except ImportError:
            pass

        try:
            return _transcribe_openai_whisper(wav_path, model_size, progress_cb)
        except ImportError:
            raise RuntimeError(
                "Topic tagging dependencies are missing. Re-run silence_trimmer.bat "
                "to provision faster-whisper and scikit-learn."
            )
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


def _transcribe_faster_whisper(
    wav_path: str, model_size: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    from faster_whisper import WhisperModel

    if progress_cb:
        progress_cb(f"Loading faster-whisper ({model_size})...")

    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    if progress_cb:
        progress_cb("Transcribing...")

    segments, _ = model.transcribe(wav_path, word_timestamps=True)

    result = []
    for seg in segments:
        result.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })
    return result


def _transcribe_openai_whisper(
    wav_path: str, model_size: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    import whisper

    if progress_cb:
        progress_cb(f"Loading whisper ({model_size})...")

    model = whisper.load_model(model_size)

    if progress_cb:
        progress_cb("Transcribing...")

    out = model.transcribe(wav_path, word_timestamps=True)

    result = []
    for seg in out.get("segments", []):
        result.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
        })
    return result


# ---------------------------------------------------------------------------
# Topic extraction via TF-IDF
# ---------------------------------------------------------------------------

# Minimal stopwords (no sklearn dependency for this list)
_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might can could and but or nor for yet "
    "so at by to in of on with from as into through during about after "
    "before between above below this that these those it its i me my we "
    "our you your he him his she her they them their what which who whom "
    "how when where why all each every both few more most other some such "
    "no not only same than too very just also then now here there again "
    "once if because until while about against between through during "
    "up down out off over under re s t d m ll ve don didn doesn wasn "
    "isn aren weren won wouldn couldn shouldn haven hasn hadn let ok "
    "really actually basically like going know think right well yeah yes".split()
)


def extract_topics_tfidf(
    transcript: list[dict],
    chunk_sec: float = 60.0,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[TopicSegment]:
    """
    Chunk transcript into windows, extract top keywords per chunk via TF-IDF.
    Falls back to simple term frequency if sklearn unavailable.
    """
    if not transcript:
        return []

    if progress_cb:
        progress_cb("Chunking transcript for topic extraction...")

    # Build chunks
    total_end = max(s["end"] for s in transcript)
    n_chunks = max(1, math.ceil(total_end / chunk_sec))

    chunks = []
    for i in range(n_chunks):
        t_start = i * chunk_sec
        t_end = (i + 1) * chunk_sec
        texts = [
            s["text"] for s in transcript
            if s["start"] >= t_start and s["start"] < t_end
        ]
        chunks.append({
            "start": t_start,
            "end": min(t_end, total_end),
            "text": " ".join(texts),
        })

    # Try sklearn TF-IDF
    try:
        return _tfidf_sklearn(chunks, progress_cb)
    except ImportError:
        return _tfidf_manual(chunks, progress_cb)


def _tfidf_sklearn(
    chunks: list[dict],
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[TopicSegment]:
    from sklearn.feature_extraction.text import TfidfVectorizer

    if progress_cb:
        progress_cb("Running TF-IDF (sklearn)...")

    texts = [c["text"] for c in chunks]
    if not any(t.strip() for t in texts):
        return []

    vec = TfidfVectorizer(
        max_features=500,
        stop_words="english",
        ngram_range=(1, 2),
    )
    tfidf_matrix = vec.fit_transform(texts)
    feature_names = vec.get_feature_names_out()

    results = []
    for i, chunk in enumerate(chunks):
        row = tfidf_matrix[i].toarray().flatten()
        top_idx = row.argsort()[-5:][::-1]
        keywords = [feature_names[j] for j in top_idx if row[j] > 0]

        if keywords:
            topic = keywords[0].title()
            results.append(TopicSegment(
                start=round(chunk["start"], 2),
                end=round(chunk["end"], 2),
                topic=topic,
                keywords=keywords[:5],
                confidence=round(float(row[top_idx[0]]), 3) if len(top_idx) > 0 else 0,
            ))

    return results


def _tfidf_manual(
    chunks: list[dict],
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[TopicSegment]:
    """Fallback: simple term frequency without sklearn."""
    if progress_cb:
        progress_cb("Running term-frequency extraction (no sklearn)...")

    # Document frequency
    n_docs = len(chunks)
    df = {}
    chunk_tfs = []

    for chunk in chunks:
        words = _tokenize(chunk["text"])
        tf = {}
        for w in words:
            tf[w] = tf.get(w, 0) + 1
        chunk_tfs.append(tf)

        for w in set(words):
            df[w] = df.get(w, 0) + 1

    results = []
    for i, chunk in enumerate(chunks):
        tf = chunk_tfs[i]
        total = sum(tf.values()) or 1

        scores = {}
        for w, count in tf.items():
            idf = math.log((n_docs + 1) / (df.get(w, 0) + 1)) + 1
            scores[w] = (count / total) * idf

        top = sorted(scores.items(), key=lambda x: -x[1])[:5]
        keywords = [w for w, _ in top]

        if keywords:
            results.append(TopicSegment(
                start=round(chunk["start"], 2),
                end=round(chunk["end"], 2),
                topic=keywords[0].title(),
                keywords=keywords,
                confidence=round(top[0][1], 3) if top else 0,
            ))

    return results


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return [w for w in words if w not in _STOPWORDS]


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def tag_video_topics(
    filepath: str,
    config: TrimConfig,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[TopicSegment]:
    """End-to-end: transcribe → chunk → extract topics."""
    transcript = transcribe_audio(filepath, config, config.whisper_model, progress_cb)

    if config.tag_method == "tfidf":
        return extract_topics_tfidf(transcript, config.tag_segment_sec, progress_cb)
    else:
        # LLM-based could be added here
        return extract_topics_tfidf(transcript, config.tag_segment_sec, progress_cb)
