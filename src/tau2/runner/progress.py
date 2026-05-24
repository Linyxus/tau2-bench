"""
Progress monitoring and retry logic for batch simulation runs.
"""

import threading
import time
import traceback
import uuid
from typing import Callable, Optional

from loguru import logger
from rich.console import Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.spinner import Spinner
from rich.table import Table

from tau2.data_model.simulation import Results, SimulationRun, TerminationReason
from tau2.data_model.tasks import Task
from tau2.metrics.agent_metrics import is_successful
from tau2.utils.display import ConsoleDisplay, Text
from tau2.utils.utils import get_now


def run_with_retry(
    run_fn: Callable[[], SimulationRun],
    task: Task,
    trial: int,
    seed: int,
    *,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    console_display: bool = True,
    save_fn: Optional[Callable[[SimulationRun], None]] = None,
    on_retry: Optional[Callable[[], None]] = None,
    shutdown_event: Optional[threading.Event] = None,
) -> SimulationRun:
    """Run a simulation function with retry logic.

    Retries on any exception. If all retries are exhausted, returns a failed
    SimulationRun with INFRASTRUCTURE_ERROR instead of raising.

    Args:
        run_fn: A callable that produces a SimulationRun.
        task: The task being run (for error reporting).
        trial: Trial number.
        seed: Random seed for this trial.
        max_retries: Maximum number of retries (on top of initial attempt).
        retry_delay: Delay in seconds between retries.
        console_display: Whether to show console output.
        save_fn: Optional callable to save the simulation after success/failure.
        on_retry: Optional callback invoked before each retry attempt.
        shutdown_event: If set, aborts retries immediately.

    Returns:
        SimulationRun (either successful or a failed placeholder).
    """
    max_attempts = max_retries + 1
    last_exception = None
    last_error_reason = ""
    last_traceback = ""

    for attempt in range(max_attempts):
        if shutdown_event is not None and shutdown_event.is_set():
            last_error_reason = "Shutdown requested (Ctrl+C)"
            last_exception = KeyboardInterrupt(last_error_reason)
            last_traceback = ""
            break

        try:
            if attempt > 0:
                retry_text = Text(
                    text=f"  Retry {attempt}/{max_retries} for task {task.id}: {last_error_reason}",
                    style="yellow",
                )
                ConsoleDisplay.console.print(retry_text)
                if on_retry:
                    on_retry()
                time.sleep(retry_delay)

            simulation = run_fn()
            simulation.trial = trial

            if console_display:
                ConsoleDisplay.display_simulation(simulation, show_details=False)
            if save_fn:
                save_fn(simulation)

            if attempt > 0:
                success_text = Text(
                    text=f"  Task {task.id} succeeded on retry {attempt}",
                    style="green",
                )
                ConsoleDisplay.console.print(success_text)

            return simulation

        except Exception as e:
            last_exception = e
            last_error_reason = str(e)
            last_traceback = traceback.format_exc()
            if attempt < max_attempts - 1:
                logger.warning(
                    f"Task {task.id} failed (attempt {attempt + 1}/{max_attempts}): {e}"
                )
            else:
                logger.error(
                    f"Task {task.id} failed after {max_attempts} attempts: {e}"
                )

    # All retries exhausted
    error_text = Text(
        text=f"  Task {task.id} failed permanently after {max_attempts} attempts: {last_error_reason}",
        style="bold red",
    )
    ConsoleDisplay.console.print(error_text)

    now = get_now()
    failed_simulation = SimulationRun(
        id=str(uuid.uuid4()),
        task_id=task.id,
        timestamp=now,
        start_time=now,
        end_time=now,
        duration=0.0,
        termination_reason=TerminationReason.INFRASTRUCTURE_ERROR,
        messages=[],
        trial=trial,
        seed=seed,
        info={
            "error": str(last_exception),
            "error_type": type(last_exception).__name__,
            "error_traceback": last_traceback,
            "failed_after_attempts": max_attempts,
        },
    )
    if save_fn:
        save_fn(failed_simulation)
    return failed_simulation


# Cap on the number of in-flight tasks shown at once (both live and fallback).
_MAX_INFLIGHT_ROWS = 10
# How often (seconds) the non-interactive fallback prints a status line.
_FALLBACK_LOG_INTERVAL = 30.0
# How often (seconds) the live display repaints (elapsed timers, spinners, ETA).
_LIVE_REFRESH_INTERVAL = 0.125


def _format_elapsed(seconds: float) -> str:
    """Format a duration in seconds as ``H:MM:SS``."""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


class _RewardColumn(ProgressColumn):
    """Renders live pass/fail counts and average reward on the overall bar."""

    def render(self, task) -> Text:
        passed = task.fields.get("passed", 0)
        failed = task.fields.get("failed", 0)
        avg = task.fields.get("avg_reward", None)
        reward_str = f"{avg:.2f}" if avg is not None else "—"
        return Text.assemble(
            ("✓", "green"),
            (f"{passed}", "green"),
            "  ",
            ("✗", "red"),
            (f"{failed}", "red"),
            ("   reward ", "dim"),
            (reward_str, "bold"),
        )


class RunProgress:
    """Live progress display for concurrent batch runs.

    On an interactive terminal, renders a region pinned to the bottom of the
    screen: an overall progress bar (percent, completed/total, elapsed, ETA,
    live pass/fail counts and average reward) plus a compact, capped list of the
    tasks currently in flight. All other console output (per-simulation panels,
    retries, logs) scrolls cleanly above it.

    On a non-interactive stream (CI, redirected to a file), it falls back to
    printing a periodic status line so logs stay readable.

    The task-tracking API mirrors the previous ``StatusMonitor`` so the batch
    runner wiring barely changes.
    """

    def __init__(
        self,
        total_count: int,
        initial_completed: int = 0,
        *,
        description: str = "Running",
        console=None,
    ):
        self.total_count = total_count
        self.completed_count = initial_completed
        self.running_tasks: dict[str, dict] = {}
        self._simulation_results: Optional[Results] = None

        self.console = console or ConsoleDisplay.console
        self._description = description
        self._use_live = bool(self.console.is_terminal)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Live-display state (interactive terminals only).
        self._live: Optional[Live] = None
        self._progress: Optional[Progress] = None
        self._overall_id = None
        self._spinner = Spinner("dots", style="green")

    def set_results(self, results: Results):
        """Set the results object used for live reward/pass-fail tracking."""
        self._simulation_results = results

    # -- task tracking -----------------------------------------------------

    def task_started(self, task_key: str, task_id: str, trial: int):
        """Record that a task has started."""
        with self._lock:
            self.running_tasks[task_key] = {
                "task_id": task_id,
                "trial": trial,
                "start_time": time.time(),
                "retries": 0,
            }

    def task_restarted(self, task_key: str):
        """Reset the start time for a task and increment its retry count."""
        with self._lock:
            if task_key in self.running_tasks:
                self.running_tasks[task_key]["start_time"] = time.time()
                self.running_tasks[task_key]["retries"] += 1

    def task_finished(self, task_key: str):
        """Record that a task has finished."""
        with self._lock:
            self.running_tasks.pop(task_key, None)
            self.completed_count += 1

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Begin the live display (TTY) or periodic status logging (non-TTY)."""
        if self._use_live:
            self._progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                TextColumn("eta"),
                TimeRemainingColumn(),
                _RewardColumn(),
                console=self.console,
                auto_refresh=False,
            )
            self._overall_id = self._progress.add_task(
                self._description,
                total=self.total_count,
                completed=self.completed_count,
                passed=0,
                failed=0,
                avg_reward=None,
            )
            self._live = Live(
                self._build_group(),
                console=self.console,
                auto_refresh=False,
                transient=False,
            )
            self._live.start(refresh=True)
            self._thread = threading.Thread(target=self._live_loop, daemon=True)
        else:
            self._thread = threading.Thread(target=self._fallback_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Tear down the live display / status thread. Idempotent."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._live is not None:
            try:
                self._live.update(self._build_group(), refresh=True)
            finally:
                self._live.stop()
            self._live = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False

    # -- rendering ---------------------------------------------------------

    def _live_loop(self):
        """Repaint the live region until stopped (drives timers/spinners/ETA)."""
        while not self._stop_event.wait(timeout=_LIVE_REFRESH_INTERVAL):
            if self._live is not None:
                try:
                    self._live.update(self._build_group(), refresh=True)
                except Exception:
                    pass

    def _compute_rewards(self) -> tuple[int, int, Optional[float]]:
        """Return ``(passed, failed, avg_reward)`` from evaluated simulations."""
        results = self._simulation_results
        if results is None:
            return 0, 0, None
        try:
            sims = list(results.simulations)
        except Exception:
            return 0, 0, None
        rewards = []
        passed = 0
        for sim in sims:
            if sim.reward_info is not None and sim.reward_info.reward is not None:
                rewards.append(sim.reward_info.reward)
                if is_successful(sim.reward_info.reward):
                    passed += 1
        failed = len(sims) - passed
        avg = (sum(rewards) / len(rewards)) if rewards else None
        return passed, failed, avg

    def _build_group(self) -> Group:
        """Build the overall bar plus the in-flight task list."""
        with self._lock:
            running = sorted(
                self.running_tasks.values(), key=lambda r: r["start_time"]
            )
            completed = self.completed_count

        passed, failed, avg = self._compute_rewards()
        if self._progress is not None and self._overall_id is not None:
            self._progress.update(
                self._overall_id,
                completed=completed,
                passed=passed,
                failed=failed,
                avg_reward=avg,
            )

        items: list = [self._progress]
        if running:
            items.append(Text(f"In flight ({len(running)})", style="cyan"))
            table = Table(box=None, show_header=False, padding=(0, 1), pad_edge=False)
            table.add_column(width=1)  # spinner
            table.add_column(no_wrap=True)  # task id
            table.add_column(no_wrap=True, style="dim")  # trial
            table.add_column(no_wrap=True, justify="right")  # elapsed
            table.add_column(no_wrap=True, style="yellow")  # retries
            now = time.time()
            for info in running[:_MAX_INFLIGHT_ROWS]:
                retries = info.get("retries", 0)
                table.add_row(
                    self._spinner,
                    str(info["task_id"]),
                    f"trial {info['trial'] + 1}",
                    _format_elapsed(now - info["start_time"]),
                    f"↻{retries}" if retries else "",
                )
            extra = len(running) - _MAX_INFLIGHT_ROWS
            if extra > 0:
                table.add_row("", Text(f"+{extra} more", style="dim"), "", "", "")
            items.append(table)
        return Group(*items)

    def _fallback_loop(self):
        """Print a status line every ``_FALLBACK_LOG_INTERVAL`` seconds (non-TTY)."""
        while not self._stop_event.wait(timeout=_FALLBACK_LOG_INTERVAL):
            with self._lock:
                running = sorted(
                    self.running_tasks.values(), key=lambda r: r["start_time"]
                )
                completed = self.completed_count
            if not running:
                continue
            now = time.time()
            statuses = []
            for info in running[:_MAX_INFLIGHT_ROWS]:
                elapsed = now - info["start_time"]
                retries = info.get("retries", 0)
                retry_str = f" R{retries}" if retries > 0 else ""
                statuses.append(
                    f"{info['task_id']}.{info['trial'] + 1}({elapsed:.0f}s{retry_str})"
                )

            _, _, avg = self._compute_rewards()
            reward_str = f"Avg reward: {avg:.2f}." if avg is not None else "Avg reward: N/A."
            extra = len(running) - _MAX_INFLIGHT_ROWS
            more = f" +{extra} more" if extra > 0 else ""
            status_text = Text(
                text=f"Status: {completed}/{self.total_count} complete. {reward_str} "
                f"{len(running)} running: {', '.join(statuses)}{more}",
                style="cyan",
            )
            self.console.print(status_text)
