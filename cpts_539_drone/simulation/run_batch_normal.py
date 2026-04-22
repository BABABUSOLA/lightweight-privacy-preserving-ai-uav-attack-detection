"""
run_batch_normal.py
===================
Batch runner for normal flight scenarios (hover + square mission).

Orchestrates multiple flight logger scripts with optional preflight checks
and automatic error reporting. Supports configurable takeoff policy.

Usage:
    python3 run_batch_normal.py
"""

import logging
import subprocess
import sys
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

PYTHON = "python3"  # or "python" depending on your VM


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


def main():
    """Main batch runner orchestration."""
    logger.info("="*70)
    logger.info("Starting batch normal flight scenarios")
    logger.info("="*70)

    # Define scenarios
    hover_logger = [PYTHON, "simulation/fly_and_log.py"]
    square_logger = [PYTHON, "simulation/mission_square_fly_and_log.py"]

    scenarios = [
        ("normal_hover", hover_logger),
        ("normal_square", square_logger),
    ]
    runs_per_scenario = 3
    duration_s = 60.0
    rate_hz = 10.0

    # Mission settings
    mission_size_m = 25.0
    mission_alt_m = 10.0
    cruise_speed_mps = 4.0

    # Takeoff policy: "never", "auto", or "always"
    takeoff_policy = "auto"

    # Determine takeoff
    do_takeoff = False
    if takeoff_policy == "always":
        do_takeoff = True
        logger.info("Takeoff policy: ALWAYS")
    elif takeoff_policy == "auto":
        logger.info("Takeoff policy: AUTO (running preflight check)")
        check_cmd = [
            PYTHON,
            "simulation/check_preflight.py",
            "--timeout-s",
            "60",
            "--arm-probe",
        ]
        preflight_ok = run_subprocess(
            check_cmd,
            timeout_s=config.TIMEOUT_SUBPROCESS_S,
            tag="PREFLIGHT",
        )
        do_takeoff = preflight_ok
        logger.info(
            f"Preflight result: {'PASS' if preflight_ok else 'FAIL'} "
            f"(takeoff will be {'enabled' if do_takeoff else 'disabled'})"
        )
    else:
        logger.info("Takeoff policy: NEVER")

    # Prepare batch runs
    total_runs = len(scenarios) * runs_per_scenario
    completed = 0
    failed = []

    logger.info(f"Total scenarios: {len(scenarios)}")
    logger.info(f"Runs per scenario: {runs_per_scenario}")
    logger.info(f"Expected total runs: {total_runs}")
    logger.info("="*70)

    # Execute batch
    for scenario_idx, (scenario, base_cmd) in enumerate(scenarios, 1):
        logger.info(f"\nScenario {scenario_idx}/{len(scenarios)}: {scenario}")
        for run_idx in range(1, runs_per_scenario + 1):
            run_id = f"{run_idx:02d}"
            tag = f"{scenario} run {run_id}"

            cmd = base_cmd + [
                "--scenario",
                scenario,
                "--run-id",
                run_id,
                "--duration",
                str(duration_s),
                "--rate",
                str(rate_hz),
            ]

            # Add mission parameters for square mission logger
            if "mission_square_fly_and_log.py" in base_cmd[-1]:
                cmd += [
                    "--mission-size-m",
                    str(mission_size_m),
                    "--mission-alt-m",
                    str(mission_alt_m),
                    "--cruise-speed-mps",
                    str(cruise_speed_mps),
                ]

            if do_takeoff:
                cmd.append("--do-takeoff")

            # Run scenario
            success = run_subprocess(cmd, tag=tag)
            if success:
                completed += 1
            else:
                failed.append(tag)

            # Progress update
            progress_pct = 100 * completed / total_runs
            logger.info(
                f"Progress: {completed}/{total_runs} ({progress_pct:.0f}%) "
                f"completed"
            )

    # Summary
    logger.info("="*70)
    logger.info("Batch execution summary:")
    logger.info(f"  Total runs: {total_runs}")
    logger.info(f"  Successful: {completed}")
    logger.info(f"  Failed: {len(failed)}")
    if failed:
        logger.error("  Failed runs:")
        for tag in failed:
            logger.error(f"    - {tag}")
    logger.info("="*70)

    return len(failed) == 0


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
    # Ensure working dir is project root when calling this script
