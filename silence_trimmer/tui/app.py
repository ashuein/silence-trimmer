"""
tui/app.py — Textual-based TUI for video silence trimmer.

Screens:
  1. ConfigScreen: folder input, parameter tuning, core selection, backend choice
  2. ProcessScreen: live per-video progress, worker pool status, overall bar
  3. ResultsScreen: summary table, quality verdicts, recommendations
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import (
    Container, Horizontal, Vertical, VerticalScroll,
)
from textual.screen import Screen
from textual.widgets import (
    Button, DataTable, DirectoryTree, Footer, Header, Input, Label,
    ProgressBar, Rule, Select, Static, Switch,
)

from ..models import TrimConfig, DetectorBackend, VIDEO_EXTENSIONS, backend_support_lines
from ..core.worker import (
    BatchProcessor,
    get_system_cores,
    discover_videos,
    recommend_parallelism,
)
from ..launcher_settings import default_silero_repo_dir, env_flag, env_text
from ..setup_silero import default_project_root, ensure_silero_repo
from ..state import load_ui_state, save_ui_state


class DirectoryPickerScreen(Screen[tuple[str, str] | None]):
    CSS = """
    #picker-root { padding: 1 2; }
    #picker-tree { height: 1fr; border: solid $surface; }
    #picker-path { margin: 1 0; color: $text-muted; }
    #picker-actions { margin-top: 1; height: 3; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, field_id: str, start_path: str | None, title: str):
        super().__init__()
        self.field_id = field_id
        self.title = title
        base_path = Path(start_path).expanduser() if start_path else Path.home()
        if base_path.is_file():
            base_path = base_path.parent
        if not base_path.exists():
            base_path = Path.home()
        self.start_path = base_path.resolve()
        self.selected_path = str(self.start_path)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="picker-root"):
            yield Label(self.title, classes="section-title")
            yield Static(self.selected_path, id="picker-path")
            yield DirectoryTree(str(self.start_path), id="picker-tree")
            with Horizontal(id="picker-actions"):
                yield Button("Use Selected", id="picker-use", variant="success")
                yield Button("Cancel", id="picker-cancel", variant="default")
        yield Footer()

    @on(DirectoryTree.DirectorySelected, "#picker-tree")
    def on_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.selected_path = str(event.path)
        self.query_one("#picker-path", Static).update(self.selected_path)

    @on(DirectoryTree.FileSelected, "#picker-tree")
    def on_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.selected_path = str(event.path.parent)
        self.query_one("#picker-path", Static).update(self.selected_path)

    @on(Button.Pressed, "#picker-use")
    def on_use(self) -> None:
        self.dismiss((self.field_id, self.selected_path))

    @on(Button.Pressed, "#picker-cancel")
    def on_cancel_button(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ═══════════════════════════════════════════════════════════════════════════
# Screen 1: Configuration
# ═══════════════════════════════════════════════════════════════════════════

class ConfigScreen(Screen):
    CSS = """
    #config-root { padding: 1 2; }
    .section-title { text-style: bold; color: $accent; margin-top: 1; }
    .param-row { height: 3; margin-bottom: 0; }
    .param-label { width: 32; padding-top: 1; }
    .param-input { width: 20; }
    .path-input { width: 1fr; }
    .warn { color: $warning; text-style: bold; }
    .info { color: $text-muted; }
    #backend-support { margin-top: 1; min-height: 5; color: $text-muted; }
    #scan-result { margin-top: 1; min-height: 3; }
    #action-row { height: 3; margin-top: 1; }
    #start-btn { width: 1fr; }
    #exit-btn { width: 1fr; }
    """

    def __init__(self):
        super().__init__()
        self.config = TrimConfig()
        self.cores_info = get_system_cores()
        self.scanned_files: list[str] = []
        self.ui_state = load_ui_state()
        launcher_silero_repo = env_text(
            "SILENCE_TRIMMER_SILERO_REPO_DIR",
            default_silero_repo_dir() or "",
        )
        if launcher_silero_repo and not os.path.isdir(launcher_silero_repo):
            launcher_silero_repo = ""
        self.launcher_defaults = {
            "backend": env_text("SILENCE_TRIMMER_DEFAULT_BACKEND", DetectorBackend.FFMPEG.value),
            "allow_model_downloads": env_flag("SILENCE_TRIMMER_ALLOW_MODEL_DOWNLOADS", False),
            "silero_repo_dir": launcher_silero_repo,
        }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="config-root"):
            # ---- Folder ----
            yield Label("── Input Folder ──", classes="section-title")
            with Horizontal(classes="param-row"):
                yield Label("Video folder path:", classes="param-label")
                yield Input(
                    value=self.ui_state.get("last_input_dir", ""),
                    placeholder="/path/to/videos",
                    id="input-dir",
                    classes="param-input path-input",
                )
                yield Button("Browse", id="browse-input-btn", variant="default")
                yield Button("Scan", id="scan-btn", variant="primary")
            yield Static("", id="scan-result")

            # ---- CPU Cores ----
            yield Label("── CPU / Workers ──", classes="section-title")
            ci = self.cores_info
            yield Static(
                f"  Physical: {ci['physical']}  |  Logical: {ci['logical']}  |  "
                f"CPU budget: {ci['usable_threads']}  |  "
                f"Recommended workers: {ci['recommended_workers']}  |  "
                f"ffmpeg threads/video: {ci['recommended_ffmpeg_threads']}",
                classes="info",
            )
            with Horizontal(classes="param-row"):
                yield Label("Worker count:", classes="param-label")
                yield Input(
                    value=str(ci["recommended_workers"]),
                    id="workers",
                    type="integer",
                    classes="param-input",
                )
            yield Static("", id="worker-warn")

            # ---- Detection Backend ----
            yield Label("── Detection Backend ──", classes="section-title")
            with Horizontal(classes="param-row"):
                yield Label("Backend:", classes="param-label")
                yield Select(
                    [(b.value, b.value) for b in DetectorBackend],
                    value=self.launcher_defaults["backend"],
                    id="backend",
                )
            with Horizontal(classes="param-row"):
                yield Label("Allow model downloads:", classes="param-label")
                yield Switch(
                    value=self.launcher_defaults["allow_model_downloads"],
                    id="allow-downloads-switch",
                )
            yield Static("", id="backend-support", classes="info")

            # ---- Silence Parameters ----
            yield Label("── Silence Detection ──", classes="section-title")
            with Horizontal(classes="param-row"):
                yield Label("Threshold (dB):", classes="param-label")
                yield Input(value="-35", id="thresh", classes="param-input")
            with Horizontal(classes="param-row"):
                yield Label("Min silence (sec):", classes="param-label")
                yield Input(value="1.0", id="min-silence", classes="param-input")
            with Horizontal(classes="param-row"):
                yield Label("Min speech (sec):", classes="param-label")
                yield Input(value="0.3", id="min-speech", classes="param-input")
            with Horizontal(classes="param-row"):
                yield Label("Padding (sec):", classes="param-label")
                yield Input(value="0.15", id="padding", classes="param-input")

            # ---- Encoding ----
            yield Label("── Encoding ──", classes="section-title")
            with Horizontal(classes="param-row"):
                yield Label("CRF (quality):", classes="param-label")
                yield Input(value="18", id="crf", type="integer", classes="param-input")
            with Horizontal(classes="param-row"):
                yield Label("Preset:", classes="param-label")
                yield Select(
                    [(p, p) for p in [
                        "ultrafast", "superfast", "veryfast", "fast",
                        "medium", "slow", "slower"
                    ]],
                    value="fast",
                    id="preset",
                )

            # ---- Topic Tagging ----
            yield Label("── Topic Tagging (optional) ──", classes="section-title")
            with Horizontal(classes="param-row"):
                yield Label("Enable tagging:", classes="param-label")
                yield Switch(value=False, id="tagging-switch")
            with Horizontal(classes="param-row"):
                yield Label("Whisper model:", classes="param-label")
                yield Select(
                    [(m, m) for m in ["tiny", "base", "small", "medium", "large"]],
                    value="base",
                    id="whisper-model",
                )
            with Horizontal(classes="param-row"):
                yield Label("Segment length (sec):", classes="param-label")
                yield Input(value="60", id="tag-segment", classes="param-input")

            # ---- Quality ----
            yield Label("── Quality Analysis ──", classes="section-title")
            with Horizontal(classes="param-row"):
                yield Label("Enable quality check:", classes="param-label")
                yield Switch(value=True, id="quality-switch")

            yield Rule()
            with Horizontal(id="action-row"):
                yield Button(
                    "▶ Start Processing", id="start-btn",
                    variant="success", disabled=True,
                )
                yield Button("Exit", id="exit-btn", variant="error")

        yield Footer()

    def on_mount(self) -> None:
        self._update_backend_support(str(self.query_one("#backend", Select).value))
        saved_input_dir = self.ui_state.get("last_input_dir", "").strip()
        if saved_input_dir and os.path.isdir(saved_input_dir):
            self._scan_path(saved_input_dir)

    @on(Button.Pressed, "#scan-btn")
    def on_scan(self) -> None:
        self._scan_path(self.query_one("#input-dir", Input).value.strip())

    @on(Button.Pressed, "#browse-input-btn")
    def on_browse_input(self) -> None:
        self.app.push_screen(
            DirectoryPickerScreen(
                "input-dir",
                self.query_one("#input-dir", Input).value.strip() or self.ui_state.get("last_input_dir"),
                "Select Input Folder",
            ),
            self._apply_browsed_path,
        )

    @on(Button.Pressed, "#exit-btn")
    def on_exit(self) -> None:
        self.app.exit()

    @on(Select.Changed, "#backend")
    def on_backend_changed(self, event: Select.Changed) -> None:
        self._update_backend_support(str(event.value))

    @on(Input.Changed, "#workers")
    def on_workers_changed(self, event: Input.Changed) -> None:
        warn = self.query_one("#worker-warn", Static)
        try:
            n = int(event.value)
            parallel = recommend_parallelism(
                logical=self.cores_info["logical"],
                physical=self.cores_info["physical"],
                requested_workers=n,
            )
            if n > self.cores_info["usable_threads"]:
                warn.update(
                    f"[yellow]Requested {n} workers exceeds the CPU budget. "
                    f"Will clamp to {parallel['workers']} with "
                    f"{parallel['ffmpeg_threads']} ffmpeg threads/video.[/yellow]"
                )
            else:
                warn.update(
                    f"[dim]Estimated CPU usage: {parallel['estimated_total_threads']}/"
                    f"{self.cores_info['logical']} logical cores "
                    f"({parallel['ffmpeg_threads']} ffmpeg threads/video)[/dim]"
                )
        except ValueError:
            warn.update("[red]Enter a valid integer[/red]")

    @on(Button.Pressed, "#start-btn")
    def on_start(self) -> None:
        # Build config from UI values
        try:
            requested_workers = int(self.query_one("#workers", Input).value)
            parallel = recommend_parallelism(
                logical=self.cores_info["logical"],
                physical=self.cores_info["physical"],
                requested_workers=requested_workers,
            )
            self.config = TrimConfig(
                silence_thresh_db=float(self.query_one("#thresh", Input).value),
                min_silence_duration=float(self.query_one("#min-silence", Input).value),
                min_speech_duration=float(self.query_one("#min-speech", Input).value),
                padding=float(self.query_one("#padding", Input).value),
                detector=DetectorBackend(self.query_one("#backend", Select).value),
                crf=int(self.query_one("#crf", Input).value),
                preset=str(self.query_one("#preset", Select).value),
                ffmpeg_threads=parallel["ffmpeg_threads"],
                max_workers=parallel["workers"],
                enable_tagging=self.query_one("#tagging-switch", Switch).value,
                whisper_model=str(self.query_one("#whisper-model", Select).value),
                tag_segment_sec=float(self.query_one("#tag-segment", Input).value),
                enable_quality=self.query_one("#quality-switch", Switch).value,
                allow_model_downloads=self.query_one("#allow-downloads-switch", Switch).value,
                silero_repo_dir=self.launcher_defaults["silero_repo_dir"] or None,
            )
        except (ValueError, TypeError) as e:
            self.notify(f"Invalid parameter: {e}", severity="error")
            return

        input_dir = self.query_one("#input-dir", Input).value.strip()
        output_dir = os.path.join(input_dir, "_trimmed_output")
        save_ui_state({
            "last_input_dir": input_dir,
        })

        self.app.push_screen(
            ProcessScreen(self.config, input_dir, output_dir, self.scanned_files)
        )

    def _scan_path(self, path: str) -> None:
        result = self.query_one("#scan-result", Static)

        if not path or not os.path.isdir(path):
            result.update("[red]Invalid directory path[/red]")
            self.query_one("#start-btn", Button).disabled = True
            return

        files = discover_videos(path)
        self.scanned_files = files
        save_ui_state({"last_input_dir": path})

        if files:
            exts = {}
            for f in files:
                ext = Path(f).suffix.lower()
                exts[ext] = exts.get(ext, 0) + 1
            ext_str = ", ".join(f"{e}: {c}" for e, c in sorted(exts.items()))
            result.update(
                f"[green]Found {len(files)} video(s)[/green]  ({ext_str})"
            )
            self.query_one("#start-btn", Button).disabled = False
        else:
            result.update(f"[yellow]No video files found in {path}[/yellow]")
            self.query_one("#start-btn", Button).disabled = True

    def _apply_browsed_path(self, result: tuple[str, str] | None) -> None:
        if not result:
            return

        field_id, path = result
        input_widget = self.query_one(f"#{field_id}", Input)
        input_widget.value = path

        if field_id == "input-dir":
            self._scan_path(path)

    def _update_backend_support(self, backend: str) -> None:
        support = self.query_one("#backend-support", Static)
        lines = backend_support_lines(backend)
        support.update("\n".join(f"  {line}" for line in lines))


# ═══════════════════════════════════════════════════════════════════════════
# Screen 2: Processing
# ═══════════════════════════════════════════════════════════════════════════

class ProcessScreen(Screen):
    CSS = """
    #process-root { padding: 1 2; }
    .overall-label { text-style: bold; margin-bottom: 1; }
    #video-table { height: 1fr; }
    #log-area { height: 8; border: solid $surface; padding: 0 1; overflow-y: auto; }
    .done-row { color: $success; }
    .err-row { color: $error; }
    """

    BINDINGS = [
        Binding("q", "cancel", "Cancel & Quit"),
    ]

    def __init__(
        self,
        config: TrimConfig,
        input_dir: str,
        output_dir: str,
        files: list[str],
    ):
        super().__init__()
        self.config = config
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.files = files
        self.processor: BatchProcessor | None = None
        self.file_status: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="process-root"):
            yield Label(
                f"Processing {len(self.files)} videos  |  "
                f"Workers: {self.config.max_workers}  |  "
                f"ffmpeg threads/video: {self.config.ffmpeg_threads}  |  "
                f"Backend: {self.config.detector.value}",
                classes="overall-label",
            )
            yield ProgressBar(total=len(self.files), id="overall-bar")
            yield Static("", id="overall-status")
            yield Rule()
            yield DataTable(id="video-table")
            yield Rule()
            yield Static("", id="log-area")
            yield Button("Cancel & Exit", id="process-exit-btn", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#video-table", DataTable)
        table.add_columns("File", "Status", "Progress", "Savings", "Detail")

        for f in self.files:
            name = os.path.basename(f)
            table.add_row(name, "QUEUED", "░░░░░░░░░░ 0%", "-", "-", key=f)
            self.file_status[f] = {"status": "queued", "pct": 0, "msg": ""}

        self._start_processing()

    @work(thread=True)
    def _start_processing(self) -> None:
        if self.config.detector == DetectorBackend.SILERO and not self.config.silero_repo_dir:
            self.app.call_from_thread(
                self._set_processing_status,
                "Preparing local Silero VAD repo...",
            )
            try:
                self.config.silero_repo_dir = str(ensure_silero_repo(default_project_root()))
            except Exception as exc:
                self.app.call_from_thread(self._processing_failed, str(exc))
                return

        self.processor = BatchProcessor(self.config, self.input_dir, self.output_dir)
        self.processor.videos = self.files
        self.processor.start()

        while self.processor.is_running:
            # Poll status updates
            updates = self.processor.poll_status()
            for u in updates:
                self.file_status[u["file"]] = u
                self.app.call_from_thread(self._update_table_row, u)

            # Collect finished
            finished = self.processor.collect_finished()
            for r in finished:
                self.app.call_from_thread(self._mark_finished, r)

            time.sleep(0.2)

        # Final collection
        finished = self.processor.collect_finished()
        for r in finished:
            self.app.call_from_thread(self._mark_finished, r)

        # Save manifest
        manifest_path = self.processor.save_manifest()
        self.app.call_from_thread(self._processing_done, manifest_path)

    def _update_table_row(self, u: dict) -> None:
        table = self.query_one("#video-table", DataTable)
        fpath = u["file"]
        name = os.path.basename(fpath)
        pct = u.get("pct", 0)
        status = u.get("status", "?").upper()
        msg = u.get("msg", "")

        # Progress bar string
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled) + f" {pct:.0f}%"

        try:
            table.update_cell(fpath, "Status", status)
            table.update_cell(fpath, "Progress", bar)
            table.update_cell(fpath, "Detail", msg[:50])
        except Exception:
            pass

        self.query_one("#log-area", Static).update(f"[{name}] {msg}")

    def _mark_finished(self, r: dict) -> None:
        table = self.query_one("#video-table", DataTable)
        fpath = r.get("input_file", "")
        status = r.get("status", "?").upper()
        savings = r.get("savings_pct", 0)
        savings_str = f"{savings:.1f}%" if status == "DONE" else "-"

        try:
            table.update_cell(fpath, "Status", status)
            table.update_cell(fpath, "Progress", "██████████ 100%")
            table.update_cell(fpath, "Savings", savings_str)

            err = r.get("error")
            if err:
                table.update_cell(fpath, "Detail", err[:50])
        except Exception:
            pass

        # Update overall bar
        bar = self.query_one("#overall-bar", ProgressBar)
        bar.advance(1)

        done = self.processor.n_done if self.processor else 0
        total = self.processor.n_total if self.processor else len(self.files)
        self.query_one("#overall-status", Static).update(
            f"  {done}/{total} complete"
        )

    def _set_processing_status(self, message: str) -> None:
        self.query_one("#overall-status", Static).update(message)
        self.query_one("#log-area", Static).update(message)

    def _processing_done(self, manifest_path: str) -> None:
        self.notify("Processing complete!", severity="information")
        self.query_one("#log-area", Static).update(
            f"[green]Done! Manifest: {manifest_path}[/green]"
        )
        # Switch to results
        if self.processor:
            self.app.push_screen(
                ResultsScreen(self.processor.results, self.config, manifest_path)
            )

    def _processing_failed(self, error: str) -> None:
        message = f"Silero setup failed: {error}"
        self.query_one("#overall-status", Static).update(f"[red]{message}[/red]")
        self.query_one("#log-area", Static).update(f"[red]{message}[/red]")
        self.notify(message, severity="error")

    def action_cancel(self) -> None:
        if self.processor:
            self.processor.shutdown()
        self.app.exit()

    @on(Button.Pressed, "#process-exit-btn")
    def on_process_exit(self) -> None:
        self.action_cancel()


# ═══════════════════════════════════════════════════════════════════════════
# Screen 3: Results
# ═══════════════════════════════════════════════════════════════════════════

class ResultsScreen(Screen):
    CSS = """
    #results-root { padding: 1 2; }
    .section-title { text-style: bold; color: $accent; margin-top: 1; }
    #results-table { height: 12; }
    #quality-area { height: 1fr; padding: 1; }
    """

    BINDINGS = [
        Binding("q", "back_to_main", "Back"),
    ]

    def __init__(self, results: list[dict], config: TrimConfig, manifest_path: str):
        super().__init__()
        self.results = results
        self.config = config
        self.manifest_path = manifest_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="results-root"):
            yield Label("── Session Summary ──", classes="section-title")
            yield Static("", id="summary-text")
            yield Rule()

            yield Label("── Per-File Results ──", classes="section-title")
            yield DataTable(id="results-table")
            yield Rule()

            yield Label("── Quality & Recommendations ──", classes="section-title")
            yield Static("", id="quality-area")
            yield Rule()

            yield Static(f"Manifest saved: {self.manifest_path}", id="manifest-path")
            yield Button("Back to Main", id="quit-btn", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        # Summary
        ok = sum(1 for r in self.results if r.get("status") == "done")
        errs = sum(1 for r in self.results if r.get("status") == "error")
        total_orig = sum(r.get("original_duration_sec", 0) for r in self.results)
        total_trim = sum(r.get("trimmed_duration_sec", 0) for r in self.results)
        saved = total_orig - total_trim

        self.query_one("#summary-text", Static).update(
            f"  Files: {len(self.results)}  |  OK: {ok}  |  Errors: {errs}\n"
            f"  Original: {total_orig/60:.1f} min  →  Trimmed: {total_trim/60:.1f} min  |  "
            f"Saved: {saved/60:.1f} min ({saved/total_orig*100:.1f}%)"
            if total_orig > 0 else "  No files processed."
        )

        # Results table
        table = self.query_one("#results-table", DataTable)
        table.add_columns(
            "File", "Status", "Duration", "Trimmed", "Saved",
            "Cuts", "Verdict",
        )

        for r in self.results:
            name = os.path.basename(r.get("input_file", "?"))
            status = r.get("status", "?").upper()
            orig = r.get("original_duration_sec", 0)
            trim = r.get("trimmed_duration_sec", 0)
            pct = r.get("savings_pct", 0)
            n_segs = len(r.get("speech_segments", []))
            q = r.get("quality", {})
            verdict = q.get("verdict", "-") if q else "-"

            table.add_row(
                name,
                status,
                f"{orig:.1f}s",
                f"{trim:.1f}s",
                f"{pct:.1f}%",
                str(n_segs),
                verdict,
            )

        # Quality recommendations
        recs_text = []
        for r in self.results:
            q = r.get("quality")
            if not q:
                continue
            name = os.path.basename(r.get("input_file", "?"))
            recommendations = q.get("recommendations", [])
            if recommendations:
                recs_text.append(f"[bold]{name}[/bold]  ({q.get('verdict', '-')})")
                for rec in recommendations:
                    recs_text.append(f"  → {rec}")
                metrics = (
                    f"  trim={q.get('trim_ratio_pct', 0):.1f}%  "
                    f"cuts/min={q.get('cuts_per_minute', 0):.1f}  "
                    f"micro_cuts={q.get('micro_cuts', 0)}  "
                    f"mean_seg={q.get('mean_speech_seg_sec', 0):.1f}s  "
                    f"boundary={q.get('boundary_energy_db', 0):.1f}dB"
                )
                recs_text.append(f"  [dim]{metrics}[/dim]")
                recs_text.append("")

        if recs_text:
            self.query_one("#quality-area", Static).update("\n".join(recs_text))
        else:
            self.query_one("#quality-area", Static).update(
                "  No quality data available."
            )

    @on(Button.Pressed, "#quit-btn")
    def on_quit(self) -> None:
        self.app.return_to_main()

    def action_back_to_main(self) -> None:
        self.app.return_to_main()


# ═══════════════════════════════════════════════════════════════════════════
# Main App
# ═══════════════════════════════════════════════════════════════════════════

class SilenceTrimmerApp(App):
    TITLE = "Video Silence Trimmer"
    SUB_TITLE = "Batch trim silence from video files"
    CSS = """
    Screen { background: $surface; }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
    ]

    def on_mount(self) -> None:
        self.push_screen(ConfigScreen())

    def return_to_main(self) -> None:
        while len(self.screen_stack) > 2:
            self.pop_screen()
