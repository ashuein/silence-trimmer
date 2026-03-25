#!/usr/bin/env python3
"""
__main__.py — Entry point for silence_trimmer.

Usage:
  # TUI mode (default)
  python -m silence_trimmer

  # CLI mode (headless / scripting)
  python -m silence_trimmer --cli /path/to/videos

  # CLI with all options
  python -m silence_trimmer --cli /path/to/videos \
      --thresh -30 --min-silence 1.5 --workers 6 \
      --backend silero-vad --tagging --whisper-model small
"""

import argparse
import os
import sys

from .launcher_settings import (
    default_silero_repo_dir,
    ensure_local_tooling_on_path,
    env_flag,
    env_text,
)
from .setup_silero import default_project_root, ensure_silero_repo


def main():
    ensure_local_tooling_on_path()
    parser = argparse.ArgumentParser(
        description="Video Silence Trimmer — batch remove dead air from videos.",
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="Run in headless CLI mode (no TUI)",
    )
    parser.add_argument("input_dir", nargs="?", default=None,
                        help="Input folder (required in --cli mode)")
    parser.add_argument("--output-dir", default=None,
                        help="Output folder (default: <input>/_trimmed_output)")
    parser.add_argument("--thresh", type=float, default=-35.0)
    parser.add_argument("--min-silence", type=float, default=1.0)
    parser.add_argument("--min-speech", type=float, default=0.3)
    parser.add_argument("--padding", type=float, default=0.15)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument(
        "--backend",
        choices=["ffmpeg", "silero-vad"],
        default=env_text("SILENCE_TRIMMER_DEFAULT_BACKEND", "ffmpeg"),
    )
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--preset", default="fast")
    parser.add_argument("--tagging", action="store_true")
    parser.add_argument("--whisper-model", default="base")
    parser.add_argument("--tag-segment", type=float, default=60.0)
    parser.add_argument(
        "--allow-model-downloads",
        action="store_true",
        default=env_flag("SILENCE_TRIMMER_ALLOW_MODEL_DOWNLOADS", False),
        help="Allow downloading external models such as Silero VAD if not already cached",
    )
    parser.add_argument(
        "--silero-repo-dir",
        default=env_text("SILENCE_TRIMMER_SILERO_REPO_DIR", default_silero_repo_dir()),
        help="Path to a local silero-vad repository for offline/local loading",
    )
    parser.add_argument(
        "--show-supported-formats",
        action="store_true",
        help="Print discovered file extensions and backend-specific audio requirements, then exit",
    )
    parser.add_argument("--no-quality", action="store_true")

    args = parser.parse_args()

    if args.show_supported_formats:
        _print_supported_formats(args.backend)
        return

    if args.cli:
        _run_cli(args)
    else:
        _run_tui()


def _run_tui():
    try:
        from .tui.app import SilenceTrimmerApp
        app = SilenceTrimmerApp()
        app.run()
    except ImportError as e:
        print(f"TUI requires 'textual'. Install: pip install textual")
        print(f"  Or run with --cli for headless mode.")
        print(f"  Error: {e}")
        sys.exit(1)


def _print_supported_formats(backend: str) -> None:
    from .models import backend_support_lines

    print(f"Supported input guidance for backend: {backend}")
    for line in backend_support_lines(backend):
        print(f"  - {line}")


def _run_cli(args):
    from .models import TrimConfig, DetectorBackend, backend_support_lines
    from .core.worker import (
        BatchProcessor,
        get_system_cores,
        discover_videos,
        recommend_parallelism,
    )

    if not args.input_dir:
        print("ERROR: input_dir required in --cli mode")
        sys.exit(1)

    if not os.path.isdir(args.input_dir):
        print(f"ERROR: {args.input_dir} is not a directory")
        sys.exit(1)

    silero_repo_dir = args.silero_repo_dir
    if silero_repo_dir and not os.path.isdir(silero_repo_dir):
        print(f"WARN: configured Silero repo not found, ignoring: {silero_repo_dir}")
        silero_repo_dir = None

    cores = get_system_cores()
    parallel = recommend_parallelism(
        logical=cores["logical"],
        physical=cores["physical"],
        requested_workers=args.workers,
    )
    n_workers = parallel["workers"]

    config = TrimConfig(
        silence_thresh_db=args.thresh,
        min_silence_duration=args.min_silence,
        min_speech_duration=args.min_speech,
        padding=args.padding,
        detector=DetectorBackend(args.backend),
        crf=args.crf,
        preset=args.preset,
        ffmpeg_threads=parallel["ffmpeg_threads"],
        max_workers=n_workers,
        enable_tagging=args.tagging,
        whisper_model=args.whisper_model,
        tag_segment_sec=args.tag_segment,
        enable_quality=not args.no_quality,
        allow_model_downloads=args.allow_model_downloads,
        silero_repo_dir=silero_repo_dir,
    )

    if config.detector == DetectorBackend.SILERO and not config.silero_repo_dir:
        try:
            config.silero_repo_dir = str(ensure_silero_repo(default_project_root()))
        except Exception as exc:
            print(f"ERROR: automatic Silero setup failed: {exc}")
            sys.exit(1)

    output_dir = args.output_dir or os.path.join(args.input_dir, "_trimmed_output")

    print(f"Input:    {args.input_dir}")
    print(f"Output:   {output_dir}")
    print(
        f"Workers:  {n_workers}  |  ffmpeg threads/video: {config.ffmpeg_threads}  |  "
        f"CPU budget: {parallel['estimated_total_threads']}/{cores['logical']} logical cores"
    )
    print(f"Backend:  {config.detector.value}")
    print(f"Thresh:   {config.silence_thresh_db}dB  |  "
          f"Min silence: {config.min_silence_duration}s  |  "
          f"Padding: {config.padding}s")
    for line in backend_support_lines(config.detector):
        print(f"Support:  {line}")
    print()

    proc = BatchProcessor(config, args.input_dir, output_dir)
    files = proc.discover()

    if not files:
        print("No video files found.")
        sys.exit(0)

    print(f"Found {len(files)} video(s). Starting...\n")
    proc.start()

    try:
        import time
        while proc.is_running:
            updates = proc.poll_status()
            for u in updates:
                name = os.path.basename(u.get("file", ""))
                print(f"  [{u.get('status', '?').upper():>20}] {name}: {u.get('msg', '')}")

            finished = proc.collect_finished()
            for r in finished:
                name = os.path.basename(r.get("input_file", ""))
                status = r.get("status", "?").upper()
                savings = r.get("savings_pct", 0)
                print(f"  [OK] {name}: {status} | saved {savings:.1f}%")

            time.sleep(0.3)

        # Final
        for r in proc.collect_finished():
            name = os.path.basename(r.get("input_file", ""))
            print(f"  [OK] {name}: {r.get('status', '?').upper()}")

    except KeyboardInterrupt:
        print("\nCancelled.")
        proc.shutdown()
        sys.exit(1)

    manifest = proc.save_manifest()
    print(f"\nManifest: {manifest}")

    # Print quality summary
    for r in proc.results:
        q = r.get("quality")
        if q and q.get("recommendations"):
            name = os.path.basename(r.get("input_file", ""))
            print(f"\n  [{name}] {q.get('verdict', '-')}")
            for rec in q["recommendations"]:
                print(f"    -> {rec}")


if __name__ == "__main__":
    main()
