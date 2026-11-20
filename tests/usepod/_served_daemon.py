"""Shared normal-daemon fixture for retained monetary tests after the vendor panel removal."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from tests.served_browser import launch_chromium
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService
from tests.usepod.test_usepod_served_flow import MODEL, UsePodServedDaemon, _free_port

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKET_ID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
MARKET = (510_000, 1_530_000)
CENTRAL = ("groq", 700_000, 2_100_000)


class PlainServedDaemon(UsePodServedDaemon):
    """The same served daemon WITHOUT the monetary test double: the authorities the panel reports
    are the production ones (unavailable), which is what the dependency lines must say."""

    def start(self, timeout: float = 240.0) -> PlainServedDaemon:
        self.seed_legacy_profile()
        self.home.mkdir(parents=True, exist_ok=True)
        env = self.env()
        env.pop("USEPOD_SERVED_DOUBLE_JOURNAL", None)
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-B", "-m", "apps.vool_api_server", "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon exited {self.process.returncode}\n{self.log_tail()}")
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        return self
            except (URLError, HTTPError, OSError):
                time.sleep(1.0)
        raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")
