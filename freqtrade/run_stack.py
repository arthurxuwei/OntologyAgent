from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import httpx

from mcp_server import main as run_mcp_server


def wait_for_freqtrade_api(base_url: str, timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    auth = httpx.BasicAuth(
        os.getenv("FREQTRADE_USERNAME", "freqtrade"),
        os.getenv("FREQTRADE_PASSWORD", "freqtrade"),
    )

    while time.time() < deadline:
        try:
            with httpx.Client(timeout=5, auth=auth) as client:
                response = client.get(f"{base_url.rstrip('/')}/ping")
            if response.status_code < 500:
                return
        except Exception:
            time.sleep(1)
            continue
        time.sleep(1)

    raise RuntimeError("Freqtrade API did not become ready in time")


def main() -> None:
    config_path = os.getenv("FREQTRADE_CONFIG_PATH", "/app/config/config.json")
    strategy_path = os.getenv("FREQTRADE_STRATEGY_PATH", "/app/strategies")
    strategy_name = os.getenv("FREQTRADE_STRATEGY_NAME", "SimpleAgentStrategy")
    api_url = os.getenv("FREQTRADE_API_URL", "http://127.0.0.1:8080/api/v1")
    ready_timeout = float(os.getenv("FREQTRADE_READY_TIMEOUT_SECONDS", "60"))
    userdir = os.getenv("FREQTRADE_USERDIR", "/app/user_data")

    Path(userdir).mkdir(parents=True, exist_ok=True)

    process = subprocess.Popen(
        [
            "freqtrade",
            "trade",
            "--userdir",
            userdir,
            "--config",
            config_path,
            "--strategy-path",
            strategy_path,
            "--strategy",
            strategy_name,
        ]
    )

    try:
        wait_for_freqtrade_api(api_url, ready_timeout)
        run_mcp_server()
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"freqtrade stack failed: {error}", file=sys.stderr)
        raise
