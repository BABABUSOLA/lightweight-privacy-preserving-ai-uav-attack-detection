"""
run_batch_normal_v2.py
======================
Advanced batch runner with scenario configuration matrix.

Supports multiple scenarios with scenario-specific overrides for:
- Duration, rate, number of runs, takeoff policy

More flexible than run_batch_normal.py for complex test matrices.

Usage:
    python3 run_batch_normal_v2.py
"""

from __future__ import annotations

import logging
import csv
import subprocess
import sys
import time
import traceback
from pathlib import Path

import config

# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(config.LOG_LEVEL)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

PYTHON = sys.executable

# ---------------------------------------------------------------------------
# Scenario configs
# Each scenario maps to a dict of overrides. Anything not listed falls back
# to the defaults defined in DEFAULTS below.
# ---------------------------------------------------------------------------
DEFAULTS = {
    "duration": 90.0,
    "rate_hz": 50.0,
    "runs": 5,
    "do_takeoff": False,
}

SCENARIOS = {
    # --- core manoeuvres ---
    "hover": {"duration": 60.0},
    "square": {"duration": 120.0},
    "altitude_step": {"duration": 60.0},

    # --- trajectory / tracking ---
    "circle": {"duration": 120.0},
    "figure_eight": {"duration": 120.0},
    "waypoint": {"duration": 120.0},

    # --- heading / rotation ---
    "yaw_spin": {"duration": 60.0},

    # --- environmental robustness ---
    "wind_gust": {"duration": 90.0},

    # --- safety ---
    "failsafe": {"duration": 45.0, "runs": 3, "do_takeoff": False},
    "landing": {"duration": 45.0},
}

# ---------------------------------------------------------------------------
# Global takeoff override.
# When True, every scenario runs with --do-takeoff UNLESS the scenario
# explicitly sets "do_takeoff": False in its own config above.
#
# Flip to True ONLY when:
#   1. Sim environment loads cleanly (vehicle spawns, sensors initialise)
#   2. A dry hover run (do_takeoff=False) logs without errors
#   3. Flight controller preflight passes (EKF converged, no sensor faults)
#   4. Arming + takeoff logic verified in a single manual test run
# ---------------------------------------------------------------------------
DO_TAKEOFF_GLOBAL = False


def get(scenario_name: str, key: str) -> float | int | bool:
    """Return a scenario-specific value or fall back to the default."""
    return SCENARIOS[scenario_name].get(key, DEFAULTS[key])


def should_takeoff(scenario: str) -> bool:
    """Decide whether a scenario should arm and take off."""
    # Per-scenario setting wins; otherwise fall back to the global flag.
    if "do_takeoff" in SCENARIOS[scenario]:
        return SCENARIOS[scenario]["do_takeoff"]
    return DO_TAKEOFF_GLOBAL


def run_subprocess(
    cmd: list, timeout_s: float = None, tag: str = ""
) -> bool:
    """
    Run a subprocess with timeout and error handling.

    Args:
        cmd: Command as list of strings.
        timeout_s: Timeout in seconds. Defaults to config.TIMEOUT_SUBPROCESS_S.
        tag: Label for logging.

    Returns:
        True if return code is 0, False otherwise.
    """
    if timeout_s is None:
        timeout_s = config.TIMEOUT_SUBPROCESS_S

    try:
        logger.info(f"[{tag}] Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            timeout=timeout_s,
            check=False,
        )
        if result.returncode == 0:
            logger.info(f"[{tag}] Success (rc=0)")
            return True
        else:
            logger.warning(
                f"[{tag}] Exited with code {result.returncode}. "
                f"Check subprocess output above."
            )
            return False
    except FileNotFoundError as e:
        logger.error(
            f"[{tag}] Script not found: {e}. "
            f"Ensure working directory is project root."
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error(
            f"[{tag}] Subprocess timed out after {timeout_s}s. "
            f"Process may still be running."
        )
        return False
    except Exception as e:
        logger.error(f"[{tag}] Unexpected error: {e}")
        logger.error(traceback.format_exc())
        return False


def find_latest_csv_since(log_dir: Path, start_time_s: float) -> Path | None:
    """Return latest CSV modified at/after start_time_s in log_dir."""
    if not log_dir.exists():
        return None

    candidates: list[Path] = []
    for path in log_dir.glob("*.csv"):
        try:
            if path.stat().st_mtime >= start_time_s:
                candidates.append(path)
        except OSError:
            continue

    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def csv_has_data_rows(csv_path: Path) -> bool:
    """Return True if CSV contains header and at least one data row."""
    try:
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            first_data_row = next(reader, None)
            return first_data_row is not None
    except Exception as e:
        logger.error(f"Failed to validate CSV file {csv_path}: {e}")
        logger.error(traceback.format_exc())
        return False


def main() -> bool:
    """Main batch runner with scenario matrix."""
    logger.info("="*70)
    logger.info("Starting batch normal flight scenarios (v2)")
    logger.info("="*70)

    base_cmd = [PYTHON, "simulation/fly_and_log.py"]
    failed_runs: list[str] = []
    log_dir = Path(config.DEFAULT_LOG_DIR)

    # Calculate total runs for progress tracking
    total_runs = sum(int(get(s, "runs")) for s in SCENARIOS)
    completed = 0

    logger.info(f"Total scenarios: {len(SCENARIOS)}")
    logger.info(f"Expected total runs: {total_runs}")
    logger.info(f"Global takeoff policy: {'ENABLED' if DO_TAKEOFF_GLOBAL else 'DISABLED'}")
    logger.info("="*70)

    for scenario in SCENARIOS:
        runs = int(get(scenario, "runs"))
        duration = float(get(scenario, "duration"))
        rate = float(get(scenario, "rate_hz"))
        takeoff = should_takeoff(scenario)

        logger.info(f"\nScenario: {scenario}")
        logger.info(
            f"  Config: duration={duration}s, rate={rate}Hz, "
            f"runs={runs}, takeoff={'YES' if takeoff else 'NO'}"
        )

        for run_idx in range(1, runs + 1):
            run_id = f"{run_idx:02d}"
            tag = f"{scenario} run {run_id}"
            cmd = base_cmd + [
                "--scenario",
                scenario,
                "--run-id",
                run_id,
                "--duration",
                str(duration),
                "--rate",
                str(rate),
            ]

            if takeoff:
                cmd.append("--do-takeoff")

            logger.info(f"\n{'='*70}")
            logger.info(f"  Run {run_id}/{runs:02d} of scenario '{scenario}'")
            logger.info(f"  Duration: {duration}s | Rate: {rate}Hz | Takeoff: {'YES' if takeoff else 'NO'}")
            logger.info(f"{'='*70}")

            # Run scenario
            run_start_s = time.time()
            success = run_subprocess(cmd, tag=tag)

            if success:
                latest_csv = find_latest_csv_since(log_dir=log_dir, start_time_s=run_start_s)
                if latest_csv is None:
                    logger.error(f"[{tag}] No CSV file created in {log_dir} after run start.")
                    success = False
                elif not csv_has_data_rows(latest_csv):
                    logger.error(f"[{tag}] CSV has no data rows (or is unreadable): {latest_csv}")
                    success = False
                else:
                    logger.info(f"[{tag}] Output validated: {latest_csv}")
            if success:
                completed += 1
            else:
                failed_runs.append(tag)

            # Progress update
            progress_pct = 100 * completed / total_runs
            logger.info(
                f"Progress: {completed}/{total_runs} ({progress_pct:.0f}%) completed"
            )

    # Summary
    logger.info("="*70)
    logger.info("Batch execution summary:")
    logger.info(f"  Total runs: {total_runs}")
    logger.info(f"  Successful: {completed}")
    logger.info(f"  Failed: {len(failed_runs)}")
    if failed_runs:
        logger.error("  Failed runs:")
        for tag in failed_runs:
            logger.error(f"    - {tag}")
    logger.info("="*70)

    return len(failed_runs) == 0


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