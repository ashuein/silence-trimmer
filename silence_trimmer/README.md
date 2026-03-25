# Video Silence Trimmer

Batch tool to detect and remove dead air from video files. Parallel processing, quality self-assessment, optional topic tagging.

The runtime auto-tunes concurrency to avoid CPU oversubscription:
it chooses a worker count and per-video `ffmpeg` thread budget from available cores.

## Architecture

```
silence_trimmer/
├── __main__.py          # Entry: TUI or --cli
├── models.py            # Dataclasses: config, results, metrics
├── core/
│   ├── detector.py      # Silence detection (ffmpeg / silero-vad)
│   ├── trimmer.py       # ffmpeg trim + concat
│   ├── quality.py       # Self-assessment without ground truth
│   ├── tagger.py        # Whisper transcription → TF-IDF topics
│   └── worker.py        # ThreadPoolExecutor orchestration
└── tui/
    └── app.py           # Textual TUI (3 screens)
```

## Install

```bash
# Core
pip install textual tqdm

# Optional: ML-based voice activity detection
pip install torch torchaudio

# Optional: topic tagging
pip install faster-whisper scikit-learn
```

### Windows launcher

Running [silence_trimmer.bat](D:/SOFTWARE_Projects_LP/silence_trimmer/silence_trimmer.bat) now does all of this automatically:

- creates or repairs `.venv_trimmer`
- installs core Python deps
- provisions local `ffmpeg` and `ffprobe` into `tools/ffmpeg` if they are not already available
- installs `torch` and `torchaudio`
- provisions a local `silero-vad` folder next to the launcher if it is missing
- installs tagging deps (`faster-whisper` and `scikit-learn`)
- reuses that local repo on later launches so the repo is not re-downloaded
- shows phase progress in the launcher, plus download/extraction progress when ZIP fallback is used

If Git is available, the launcher uses `git clone` and shows Git's native clone progress.
If Git is not available, it downloads the GitHub ZIP and extracts it with PowerShell's
`System.IO.Compression.ZipFile` API while reporting extraction progress entry by entry.

## Usage

```bash
# TUI mode
python -m silence_trimmer

# CLI mode (headless)
python -m silence_trimmer --cli /path/to/videos

# CLI with tuning
python -m silence_trimmer --cli ./lectures/ \
    --thresh -30 --min-silence 1.5 --padding 0.2 \
    --workers 6 --backend silero-vad \
    --tagging --whisper-model small

# Use the provisioned local Silero repo
python -m silence_trimmer --cli ./lectures/ \
    --backend silero-vad
```

## Output Structure

```
input_folder/
├── lecture_01.mp4
├── lecture_02.mkv
└── _trimmed_output/           ← created automatically
    ├── lecture_01_trimmed.mp4
    ├── lecture_02_trimmed.mp4
    └── _session_manifest.json  ← metadata for all files
```

### Manifest contains per-file:
- All silence intervals (above AND below threshold)
- Speech segment timestamps
- Quality metrics + recommendations
- Topic segments (if tagging enabled)

## Detection Backends

| Backend | Mechanism | Pros | Cons |
|---------|-----------|------|------|
| `ffmpeg` (default) | Amplitude threshold | Zero extra deps, fast | Fooled by background noise/music |
| `silero-vad` | Neural voice activity detection | Accurate speech vs non-speech | Requires torch (~2GB), slower |

When launched from the Windows `.bat`, Silero uses the auto-provisioned local repo directory.

## Supported Input Guidance

Discoverable file extensions:

` .avi, .flv, .m4v, .mkv, .mov, .mp4, .mts, .ts, .webm, .wmv `

General rule:

- Files are only scanned if their extension matches the list above.
- After discovery, the file still must be decodable by `ffmpeg`.
- Lowest-risk inputs are `.mp4`, `.mkv`, `.mov`, or `.webm` with standard audio such as `aac`, `mp3`, `pcm`, `opus`, or `vorbis`.

Backend-specific notes:

- `ffmpeg`: requires at least one audio stream, because silence detection runs on audio.
- `silero-vad`: also requires at least one audio stream, and that audio must be extractable by `ffmpeg` to mono 16 kHz WAV first.
- Video-only files are not valid inputs for silence detection and will be skipped.

CLI tip:

```bash
python -m silence_trimmer --show-supported-formats --backend ffmpeg
python -m silence_trimmer --show-supported-formats --backend silero-vad
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## Quality Self-Assessment

Without labeled ground truth, the tool detects pathological parameter settings:

| Metric | What it catches | Recommended action |
|--------|----------------|--------------------|
| Trim ratio <2% | Threshold too low | Raise to -30dB |
| Trim ratio >60% | Over-aggressive | Lower to -40dB or raise min_silence |
| Micro-cuts (>3 segments <2s) | Clipping mid-sentence | Increase min_silence or padding |
| Cuts/min >10 | Choppy playback | Increase min_silence |
| High boundary energy | Cutting into speech | Increase padding |
| Low silence headroom | Threshold in noise floor | Use silero-vad instead |

## Parameter Tuning Guide

| Content type | thresh | min_silence | padding | Notes |
|---|---|---|---|---|
| Clean lecture (studio) | -40 dB | 1.0s | 0.15s | Default works well |
| Lecture (auditorium) | -30 dB | 1.5s | 0.25s | Room noise raises floor |
| Interview/podcast | -35 dB | 0.8s | 0.10s | Shorter pauses are natural |
| Conference recording | -25 dB | 2.0s | 0.30s | High ambient noise |
| Screen recording | -45 dB | 0.5s | 0.10s | Very clean audio |

## Topic Tagging

When `--tagging` is enabled:

1. Audio extracted → Whisper transcription (word-level timestamps)
2. Transcript chunked into segments (default 60s windows)
3. TF-IDF keyword extraction per segment
4. Saved in manifest as topic_segments with timestamps + keywords

Useful for: lecture indexing, content navigation, search metadata.
