"""
Master Pipeline Runner
======================
Runs the full data-collection pipeline sequentially.

Includes two spoofing options:
  (A) Data-level spoofing (CSV-only): simulation/gps_spoof_fly_and_log.py
  (B) System-level spoofing (PX4-facing): simulation/gps_input_spoofer.py + a moving flight logger

Usage:
    python3 run_pipeline.py
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import List, Optional

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
    # Step 1: Normal flights (hover + square) via run_batch_normal.py
    # run_batch_normal.py handles its own internal loop, so runs=1.
    Step(
        name="Normal flight batch (hover + square)",
        script="simulation/run_batch_normal.py",
        inject_run_id=False,
    ),
    # Step 2: Threat-model-aligned spoofing (system-level): GPS_INPUT injection
    # Run the spoofer alongside the square mission logger so the vehicle moves.
    SpoofedMissionStep(
        name="GPS_INPUT spoof + square mission (slow drift NE)",
        spoofer_script="simulation/gps_input_spoofer.py",
        spoofer_args=[
            "--master",
            "udp:127.0.0.1:14540",
            "--rate-hz",
            "10",
            "--attack-start",
            "10",
            "--drift-rate-m-per-s",
            "1.5",
            "--drift-direction-deg",
            "45",
        ],
        flight_script="simulation/mission_square_fly_and_log.py",
        flight_args=[
            "--scenario",
            "attack_square_gpsinput_drift",
            "--duration",
            "90",
            "--rate",
            "10",
            "--mission-size-m",
            "25",
            "--mission-alt-m",
            "10",
            "--cruise-speed-mps",
            "4",
            "--do-takeoff",
        ],
        runs=3,
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
        runs=3,
        inject_run_id=True,
    ),
]


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------
def run_script_step(step: Step, run_num: int) -> bool:
    cmd = [PYTHON, step.script] + step.args
    if step.inject_run_id and step.runs > 1:
        cmd += ["--run-id", f"{run_num:02d}"]

    print(f"  Command : {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"  [WARN] Exited with code {result.returncode}")
            return False
        return True
    except FileNotFoundError:
        print(
            f"  [ERROR] Could not find '{cmd[0]}' or '{cmd[1]}' — check your paths"
        )
        return False
    except Exception as exc:
        print(f"  [ERROR] {type(exc).__name__}: {exc}")
        return False


def run_spoofed_mission_step(step: SpoofedMissionStep, run_num: int) -> bool:
    spoofer_cmd = [PYTHON, step.spoofer_script] + step.spoofer_args
    flight_cmd = [PYTHON, step.flight_script] + step.flight_args
    if step.inject_run_id and step.runs > 1:
        flight_cmd += ["--run-id", f"{run_num:02d}"]

    print(f"  Spoofer : {' '.join(spoofer_cmd)}")
    print(f"  Flight  : {' '.join(flight_cmd)}")

    spoofer_proc: Optional[subprocess.Popen] = None
    try:
        spoofer_proc = subprocess.Popen(spoofer_cmd)
        time.sleep(step.spoofer_warmup_s)

        result = subprocess.run(flight_cmd)
        if result.returncode != 0:
            print(f"  [WARN] Flight exited with code {result.returncode}")
            return False
        return True
    except FileNotFoundError:
        print("  [ERROR] Could not find python or one of the scripts — check paths")
        return False
    except Exception as exc:
        print(f"  [ERROR] {type(exc).__name__}: {exc}")
        return False
    finally:
        if spoofer_proc is not None and spoofer_proc.poll() is None:
            spoofer_proc.terminate()
            try:
                spoofer_proc.wait(timeout=5)
            except Exception:
                spoofer_proc.kill()


def main() -> None:
    total_runs = 0
    for s in PIPELINE:
        total_runs += getattr(s, "runs", 1)

    passed = 0
    failed_tags: list[str] = []

    print(f"\n{'#'*60}")
    print(f"  PIPELINE START — {len(PIPELINE)} steps, {total_runs} total runs")
    print(f"{'#'*60}")

    pipeline_start = time.time()

    for step_idx, step in enumerate(PIPELINE, start=1):
        step_runs = getattr(step, "runs", 1)
        print(f"\n{'='*60}")
        print(f"  Step {step_idx}/{len(PIPELINE)}: {getattr(step, 'name', 'Unnamed')}")
        print(f"  Runs   : {step_runs}")
        print(f"{'='*60}")

        for run_num in range(1, step_runs + 1):
            tag = (
                f"{step.name} (run {run_num:02d})" if step_runs > 1 else step.name
            )
            print(f"\n  --- {tag} ---")

            run_start = time.time()
            if isinstance(step, SpoofedMissionStep):
                ok = run_spoofed_mission_step(step, run_num)
            else:
                ok = run_script_step(step, run_num)
            elapsed = time.time() - run_start

            if ok:
                passed += 1
                print(f"  Finished in {elapsed:.1f}s")
            else:
                failed_tags.append(tag)
                print(f"  Failed after {elapsed:.1f}s")

    total_elapsed = time.time() - pipeline_start
    mins, secs = divmod(int(total_elapsed), 60)

    print(f"\n{'#'*60}")
    print(f"  PIPELINE COMPLETE — {mins}m {secs}s total")
    print(f"  Passed : {passed}/{total_runs}")
    if failed_tags:
        print(f"  Failed : {len(failed_tags)}")
        for tag in failed_tags:
            print(f"    - {tag}")
    else:
        print("  All steps succeeded.")
    print(f"{'#'*60}\n")


if __name__ == "__main__":
    main()

