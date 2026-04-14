"""
Master Pipeline Runner
======================
Runs the full data-collection pipeline sequentially:

  Step 1 — Normal flight batch   (run_batch_normal.py)
  Step 2 — GPS spoofing flights  (gps_spoof_fly_and_log.py × N configs)

Usage:
    python3 run_pipeline.py

Add or remove steps by editing the PIPELINE list below.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import List

PYTHON = "python3"


# ---------------------------------------------------------------------------
# Step definition
# ---------------------------------------------------------------------------
@dataclass
class Step:
    """One unit of work in the pipeline."""
    name: str                          # human-readable label
    script: str                        # path to the .py file
    args: list[str] = field(default_factory=list)  # CLI args
    runs: int = 1                      # how many times to repeat


# ---------------------------------------------------------------------------
# Pipeline — edit this list to add / remove / reorder steps
# ---------------------------------------------------------------------------
PIPELINE: List[Step] = [

    # ── Step 1: Normal flights ───────────────────────────────────────────
    # run_batch_normal.py handles its own internal loop, so runs=1.
    Step(
        name="Normal flight batch",
        script="simulation/run_batch_normal.py",
    ),

    # ── Step 2: GPS spoofing flights ─────────────────────────────────────
    # Each config is its own step so you can tune args independently.

    # Slow drift, northeast
    Step(
        name="GPS spoof — slow drift NE",
        script="simulation/gps_spoof_fly_and_log.py",
        args=[
            "--scenario", "spoof_drift",
            "--duration", "60",
            "--rate", "10",
            "--attack-start", "10",
            "--drift-rate-m-per-s", "1.5",
            "--drift-direction-deg", "45",
            "--do-takeoff",
        ],
        runs=3,
    ),

    # Fast drift, east
    Step(
        name="GPS spoof — fast drift E",
        script="simulation/gps_spoof_fly_and_log.py",
        args=[
            "--scenario", "spoof_fast",
            "--duration", "60",
            "--rate", "10",
            "--attack-start", "15",
            "--drift-rate-m-per-s", "3.0",
            "--drift-direction-deg", "90",
            "--do-takeoff",
        ],
        runs=3,
    ),

    # Late-onset drift, south
    Step(
        name="GPS spoof — late onset S",
        script="simulation/gps_spoof_fly_and_log.py",
        args=[
            "--scenario", "spoof_late",
            "--duration", "90",
            "--rate", "10",
            "--attack-start", "40",
            "--drift-rate-m-per-s", "2.0",
            "--drift-direction-deg", "180",
            "--do-takeoff",
        ],
        runs=3,
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run_step(step: Step, run_num: int) -> bool:
    """Execute a single run of a step. Returns True if it succeeded."""
    cmd = [PYTHON, step.script] + step.args
    if step.runs > 1:
        cmd += ["--run-id", f"{run_num:02d}"]

    print(f"  Command : {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"  [WARN] Exited with code {result.returncode}")
            return False
        return True

    except FileNotFoundError:
        print(f"  [ERROR] Could not find '{cmd[0]}' or '{cmd[1]}' — "
              f"check your paths")
        return False

    except Exception as exc:
        print(f"  [ERROR] {type(exc).__name__}: {exc}")
        return False


def main() -> None:
    total_steps = sum(s.runs for s in PIPELINE)
    passed = 0
    failed_tags: list[str] = []

    print(f"\n{'#'*60}")
    print(f"  PIPELINE START — {len(PIPELINE)} steps, {total_steps} total runs")
    print(f"{'#'*60}")

    pipeline_start = time.time()

    for step_idx, step in enumerate(PIPELINE, start=1):
        print(f"\n{'='*60}")
        print(f"  Step {step_idx}/{len(PIPELINE)}: {step.name}")
        print(f"  Script : {step.script}")
        print(f"  Runs   : {step.runs}")
        print(f"{'='*60}")

        for run_num in range(1, step.runs + 1):
            tag = f"{step.name} (run {run_num:02d})" if step.runs > 1 else step.name
            print(f"\n  --- {tag} ---")

            run_start = time.time()
            ok = run_step(step, run_num)
            elapsed = time.time() - run_start

            if ok:
                passed += 1
                print(f"  Finished in {elapsed:.1f}s")
            else:
                failed_tags.append(tag)
                print(f"  Failed after {elapsed:.1f}s")

    # ── summary ──
    total_elapsed = time.time() - pipeline_start
    mins, secs = divmod(int(total_elapsed), 60)

    print(f"\n{'#'*60}")
    print(f"  PIPELINE COMPLETE — {mins}m {secs}s total")
    print(f"  Passed : {passed}/{total_steps}")

    if failed_tags:
        print(f"  Failed : {len(failed_tags)}")
        for tag in failed_tags:
            print(f"    • {tag}")
    else:
        print("  All steps succeeded.")

    print(f"{'#'*60}\n")


if __name__ == "__main__":
    main()