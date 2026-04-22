"""
Master Pipeline Runner
======================
Runs the full data-collection pipeline sequentially.

Includes two spoofing options:
  (A) Real-time GPS spoof logging on out-and-back mission
  (B) Data-level spoofing (CSV-only): simulation/gps_spoof_fly_and_log.py

Usage:
    python3 run_pipeline.py
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SIMULATION_DIR = Path(__file__).resolve().parent / "simulation"
if str(SIMULATION_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATION_DIR))

import config

logger = logging.getLogger(__name__)
logger.setLevel(config.LOG_LEVEL)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

PYTHON = "python3"


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------
@dataclass
class Step:
    """One unit of work in the pipeline."""

    name: str
    script: str
    args: list[str] = field(default_factory=list)
    runs: int = 1
    inject_run_id: bool = True  # if True, add --run-id when runs > 1


@dataclass
class SpoofedMissionStep:
    """
    Run a moving flight logger while a GPS_INPUT spoofer runs in background.

    This is the threat-model-aligned variant: PX4 consumes spoofed GPS_INPUT.
    """

    name: str
    spoofer_script: str
    spoofer_args: list[str]
    flight_script: str
    flight_args: list[str]
    runs: int = 1
    inject_run_id: bool = True  # applies to flight logger only
    spoofer_warmup_s: float = 2.0  # give spoofer time to start sending messages


# ---------------------------------------------------------------------------
# Pipeline — edit this list to add / remove / reorder steps
# ---------------------------------------------------------------------------
PIPELINE: list[object] = [
    # Step 1: Normal out-and-back mission
    Step(
        name="Normal out-and-back fast mission",
        script="simulation/mission_out_back_fly_and_log.py",
        args=[
            "--scenario",
            "out_back_fast_10s",
            "--cruise-seconds",
            "10",
            "--cruise-speed-mps",
            "12",
            "--mission-alt-m",
            "12",
            "--do-takeoff",
        ],
        runs=1
        inject_run_id=True,
    ),
    # Step 2: Real-time GPS spoofing on out-and-back mission
    Step(
        name="Out-and-back real-time spoof (fast drift E)",
        script="simulation/mission_out_back_gps_spoof_fly_and_log.py",
        args=[
            "--scenario",
            "out_back_fast_10s_spoof",
            "--cruise-seconds",
            "10",
            "--cruise-speed-mps",
            "12",
            "--mission-alt-m",
            "12",
            "--attack-start",
            "3",
            "--drift-rate-m-per-s",
            "4.0",
            "--drift-direction-deg",
            "90",
            "--do-takeoff",
        ],
        runs=1
        inject_run_id=True,
    ),
    # Step 3: Baseline spoofing (data-level): CSV-only drift (kept for reproducibility)
    Step(
        name="CSV spoof — slow drift NE (baseline)",
        script="simulation/gps_spoof_fly_and_log.py",
        args=[
            "--scenario",
            "spoof_drift",
            "--duration",
            "60",
            "--rate",
            "10",
            "--attack-start",
            "10",
            "--drift-rate-m-per-s",
            "1.5",
            "--drift-direction-deg",
            "45",
            "--do-takeoff",
        ],
        runs=1
        inject_run_id=True,
    ),
]


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------
def run_subprocess(
    cmd: list[str], timeout_s: float | None = None, tag: str = ""
) -> bool:
    """Run a subprocess with timeout and robust error handling."""
    if timeout_s is None:
        timeout_s = config.TIMEOUT_SUBPROCESS_S

    try:
        logger.info(f"[{tag}] Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, timeout=timeout_s, check=False)
        if result.returncode == 0:
            logger.info(f"[{tag}] Success (rc=0)")
            return True
        logger.warning(f"[{tag}] Exited with code {result.returncode}")
        return False
    except FileNotFoundError as exc:
        logger.error(f"[{tag}] Script not found: {exc}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"[{tag}] Timed out after {timeout_s:.1f} seconds")
        return False
    except Exception as exc:
        logger.error(f"[{tag}] Unexpected error: {exc}")
        logger.error(traceback.format_exc())
        return False


def run_script_step(step: Step, run_num: int) -> bool:
    cmd = [PYTHON, step.script] + step.args
    if step.inject_run_id and step.runs > 1:
        cmd += ["--run-id", f"{run_num:02d}"]
    logger.info(f"  Command : {' '.join(cmd)}")
    return run_subprocess(cmd, tag=f"{step.name} run {run_num:02d}")


def run_spoofed_mission_step(step: SpoofedMissionStep, run_num: int) -> bool:
    spoofer_cmd = [PYTHON, step.spoofer_script] + step.spoofer_args
    flight_cmd = [PYTHON, step.flight_script] + step.flight_args
    if step.inject_run_id and step.runs > 1:
        flight_cmd += ["--run-id", f"{run_num:02d}"]

    logger.info(f"  Spoofer : {' '.join(spoofer_cmd)}")
    logger.info(f"  Flight  : {' '.join(flight_cmd)}")

    spoofer_proc: Optional[subprocess.Popen] = None
    try:
        spoofer_proc = subprocess.Popen(spoofer_cmd)
        time.sleep(step.spoofer_warmup_s)
        if spoofer_proc.poll() is not None:
            logger.error(
                "  [ERROR] Spoofer exited early with code "
                f"{spoofer_proc.returncode}"
            )
            return False

        result = subprocess.run(
            flight_cmd,
            timeout=config.TIMEOUT_SUBPROCESS_S,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(f"  [WARN] Flight exited with code {result.returncode}")
            return False
        return True
    except FileNotFoundError as exc:
        logger.error(f"  [ERROR] Could not find python or script: {exc}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(
            f"  [ERROR] Flight timed out after {config.TIMEOUT_SUBPROCESS_S:.1f}s"
        )
        return False
    except Exception as exc:
        logger.error(f"  [ERROR] {type(exc).__name__}: {exc}")
        logger.error(traceback.format_exc())
        return False
    finally:
        if spoofer_proc is not None and spoofer_proc.poll() is None:
            spoofer_proc.terminate()
            try:
                spoofer_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("  [WARN] Spoofer did not terminate cleanly; killing.")
                spoofer_proc.kill()
                try:
                    spoofer_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("  [WARN] Spoofer still alive after kill attempt.")


def main() -> bool:
    total_runs = 0
    for s in PIPELINE:
        total_runs += getattr(s, "runs", 1)

    passed = 0
    failed_tags: list[str] = []

    logger.info(f"\n{'#'*60}")
    logger.info(f"  PIPELINE START — {len(PIPELINE)} steps, {total_runs} total runs")
    logger.info(f"{'#'*60}")

    pipeline_start = time.time()

    for step_idx, step in enumerate(PIPELINE, start=1):
        step_runs = getattr(step, "runs", 1)
        logger.info(f"\n{'='*60}")
        logger.info(
            f"  Step {step_idx}/{len(PIPELINE)}: {getattr(step, 'name', 'Unnamed')}"
        )
        logger.info(f"  Runs   : {step_runs}")
        logger.info(f"{'='*60}")

        for run_num in range(1, step_runs + 1):
            tag = (
                f"{step.name} (run {run_num:02d})" if step_runs > 1 else step.name
            )
            logger.info(f"\n  --- {tag} ---")

            run_start = time.time()
            if isinstance(step, SpoofedMissionStep):
                ok = run_spoofed_mission_step(step, run_num)
            else:
                ok = run_script_step(step, run_num)
            elapsed = time.time() - run_start

            if ok:
                passed += 1
                logger.info(f"  Finished in {elapsed:.1f}s")
            else:
                failed_tags.append(tag)
                logger.info(f"  Failed after {elapsed:.1f}s")

    total_elapsed = time.time() - pipeline_start
    mins, secs = divmod(int(total_elapsed), 60)

    logger.info(f"\n{'#'*60}")
    logger.info(f"  PIPELINE COMPLETE — {mins}m {secs}s total")
    logger.info(f"  Passed : {passed}/{total_runs}")
    if failed_tags:
        logger.info(f"  Failed : {len(failed_tags)}")
        for tag in failed_tags:
            logger.info(f"    - {tag}")
    else:
        logger.info("  All steps succeeded.")
    logger.info(f"{'#'*60}\n")

    return len(failed_tags) == 0


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

