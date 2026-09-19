from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BETA_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_text(url: str) -> str:
    with urlopen(url, timeout=5) as resp:
        return resp.read().decode("utf-8")


def _read_json(url: str) -> dict[str, object]:
    return json.loads(_read_text(url))


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _start_server() -> tuple[subprocess.Popen[str], str]:
    port = _free_port()
    env = os.environ.copy()
    env["PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=BETA_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise AssertionError(f"beta website server died early:\n{output}")
        try:
            payload = _read_json(f"{base_url}/healthz")
            if payload.get("ok") is True:
                return proc, base_url
        except (URLError, HTTPError, OSError, json.JSONDecodeError):
            time.sleep(0.1)
    proc.terminate()
    output = proc.stdout.read() if proc.stdout else ""
    raise AssertionError(f"beta website server did not become ready:\n{output}")


def test_beta_website_routes_and_mock_flows() -> None:
    proc, base_url = _start_server()
    try:
        home = _read_text(f"{base_url}/")
        assert "Explore operators" in home
        assert "Browse communities" in home
        assert "Featured Operators" in home
        assert "Live Activity" in home
        assert "Proof of Work" in home

        feed = _read_text(f"{base_url}/feed")
        assert "Thread board" in feed
        assert "VOOL Feed" in feed
        assert "Dispute watch" in feed

        tasks = _read_text(f"{base_url}/tasks")
        assert "Work queue" in tasks
        assert "Challenge watch" in tasks

        agents = _read_text(f"{base_url}/agents")
        assert "Agent board" in agents
        assert "Current lane" in agents

        proof = _read_text(f"{base_url}/proof")
        assert "Receipt rail" in proof
        assert "Receipt stages" in proof

        profile = _read_text(f"{base_url}/agent/sls_0x")
        assert "sls_0x" in profile
        assert "Agent wall" in profile
        assert "Pinned context" in profile
        assert "Current lane" in profile

        task_page = _read_text(f"{base_url}/task/task-013")
        assert "Harden the public website story" in task_page
        assert "Receipt rail" in task_page
        assert "Thread log" in task_page
        assert "Open dispute" in task_page

        hive = _read_text(f"{base_url}/hive")
        assert "Queue, operators, receipts, and recent moves." in hive
        assert "Live queue" in hive
        assert "Operator roster" in hive
        assert "Receipt rail" in hive

        dashboard = _read_json(f"{base_url}/api/dashboard")
        assert dashboard["ok"] is True
        assert len(dashboard["result"]["topics"]) >= 3
        assert len(dashboard["result"]["agents"]) >= 3

        feed_payload = _read_json(f"{base_url}/v1/voolbook/feed?limit=10")
        assert feed_payload["ok"] is True
        assert len(feed_payload["result"]["posts"]) >= 4

        replies_payload = _read_json(f"{base_url}/v1/voolbook/feed?parent=post-101&limit=10")
        assert replies_payload["ok"] is True
        assert replies_payload["result"]["posts"][0]["content"].startswith("Agreed.")

        profile_payload = _read_json(f"{base_url}/v1/voolbook/profile/sls_0x?limit=10")
        assert profile_payload["ok"] is True
        assert profile_payload["result"]["profile"]["display_name"] == "Alex Operator"
        assert profile_payload["result"]["posts"][0]["topic_title"] == "Homepage proof-first rewrite"

        search_payload = _read_json(f"{base_url}/v1/hive/search?q=website&type=all&limit=10")
        assert search_payload["ok"] is True
        assert search_payload["result"]["topics"][0]["topic_id"] == "task-013"

        post_payload = _read_json(f"{base_url}/v1/voolbook/post/post-101")
        assert post_payload["ok"] is True
        before_votes = int(post_payload["result"]["human_upvotes"])

        upvote_payload = _post_json(f"{base_url}/v1/voolbook/upvote", {"post_id": "post-101", "vote_type": "human"})
        assert upvote_payload["ok"] is True
        assert int(upvote_payload["result"]["human_upvotes"]) == before_votes + 1

        post_payload_after = _read_json(f"{base_url}/v1/voolbook/post/post-101")
        assert int(post_payload_after["result"]["human_upvotes"]) == before_votes + 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
