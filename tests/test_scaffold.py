from __future__ import annotations

import os
import subprocess
import sys


def test_installed_cli_exposes_product_commands_and_defaults_offline() -> None:
    environment = os.environ.copy()
    environment.pop("MONOTONE_CALIBRATE_LLM_ENABLED", None)
    completed = subprocess.run(
        [sys.executable, "-m", "monotone_calibrate", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0
    assert "run" in completed.stdout
    assert "predict" in completed.stdout
    assert "verify" in completed.stdout
    assert "LLM advisor: disabled by default" in completed.stdout
