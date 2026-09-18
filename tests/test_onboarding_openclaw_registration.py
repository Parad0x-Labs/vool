from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.onboarding as onboarding


class OnboardingOpenClawRegistrationTests(unittest.TestCase):
    def test_ensure_openclaw_registration_repairs_missing_vool_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_home = Path(tmpdir) / ".openclaw"
            config_path = openclaw_home / "openclaw.json"
            openclaw_home.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "agents": {
                            "defaults": {
                                "workspace": "/existing/workspace",
                                "model": {"primary": "ollama/qwen2.5:7b"},
                            }
                        },
                        "gateway": {"auth": {"mode": "token", "token": "keep-me"}},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.dict("os.environ", {"OPENCLAW_HOME": str(openclaw_home)}, clear=False), mock.patch.object(
                onboarding, "PROJECT_ROOT", Path("/tmp/vool-project")
            ), mock.patch.object(onboarding, "active_vool_home", return_value=Path("/tmp/vool-runtime")):
                ok = onboarding.ensure_openclaw_registration(
                    display_name="Cornholio",
                    model_tag="qwen2.5:14b",
                )

            self.assertTrue(ok)
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(cfg["gateway"]["auth"]["token"], "keep-me")
            self.assertEqual(cfg["agents"]["list"][0]["id"], "vool")
            self.assertEqual(cfg["agents"]["list"][0]["name"], "Cornholio")
            self.assertEqual(
                cfg["agents"]["list"][0]["agentDir"],
                str(openclaw_home / "agents" / "vool" / "agent"),
            )

    def test_load_openclaw_agent_name_uses_config_path_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "portable" / "openclaw.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "agents": {
                            "list": [
                                {
                                    "id": "vool",
                                    "name": "Portable VOOL",
                                }
                            ]
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.dict(
                "os.environ",
                {
                    "OPENCLAW_CONFIG_PATH": str(config_path),
                    "OPENCLAW_HOME": "",
                },
                clear=False,
            ):
                self.assertEqual(onboarding._load_openclaw_agent_name(), "Portable VOOL")


if __name__ == "__main__":
    unittest.main()
