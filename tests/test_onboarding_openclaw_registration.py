"""Retirement contract: first-boot onboarding never touches OpenClaw state.

The OpenClaw integration is retired from the VOOL product. These tests pin the
boundary that used to be the registration bridge: saving/renaming an identity and
bootstrapping first boot must not read, create, or rewrite any OpenClaw config,
and an unrelated existing OpenClaw installation must stay byte-for-byte intact.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.onboarding as onboarding


class OnboardingOpenClawRetirementTests(unittest.TestCase):
    def test_onboarding_exposes_no_openclaw_registration_surface(self) -> None:
        for retired_name in (
            "ensure_openclaw_registration",
            "_sync_name_to_openclaw",
            "_load_openclaw_agent_name",
        ):
            self.assertFalse(
                hasattr(onboarding, retired_name),
                f"core.onboarding.{retired_name} must not exist after OpenClaw retirement",
            )

    def test_save_identity_leaves_unrelated_openclaw_config_byte_for_byte_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_home = Path(tmpdir) / ".openclaw"
            config_path = openclaw_home / "openclaw.json"
            openclaw_home.mkdir(parents=True, exist_ok=True)
            original_config = json.dumps(
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
            ) + "\n"
            config_path.write_text(original_config, encoding="utf-8")

            with mock.patch.dict(
                "os.environ",
                {"OPENCLAW_HOME": str(openclaw_home), "VOOL_HOME": str(Path(tmpdir) / "vool-home")},
                clear=False,
            ), mock.patch.object(
                onboarding, "data_path", side_effect=lambda name: Path(tmpdir) / "vool-home" / "data" / name
            ), mock.patch.object(
                onboarding, "_ensure_voolbook_registration", return_value=None
            ):
                path = onboarding.save_identity(agent_name="Cornholio", privacy_pact="stay local")
                onboarding.force_rename("NewName")

            self.assertTrue(path.is_file())
            identity = json.loads((Path(tmpdir) / "vool-home" / "data" / "owner_identity.json").read_text(encoding="utf-8"))
            self.assertEqual(identity["agent_name"], "NewName")
            self.assertEqual(config_path.read_text(encoding="utf-8"), original_config)
            self.assertEqual(
                sorted(item.name for item in openclaw_home.iterdir()),
                ["openclaw.json"],
                "onboarding must not create additional OpenClaw files",
            )

    def test_ensure_bootstrap_identity_ignores_valid_unrelated_openclaw_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_home = Path(tmpdir) / ".openclaw"
            config_path = openclaw_home / "openclaw.json"
            openclaw_home.mkdir(parents=True, exist_ok=True)
            # A VALID unrelated OpenClaw config naming a VOOL entry: after retirement it
            # must not influence VOOL's chosen name and must not be rewritten.
            original_config = json.dumps({"agents": {"list": [{"id": "vool", "name": "Portable VOOL"}]}})
            config_path.write_text(original_config, encoding="utf-8")

            identity_dir = Path(tmpdir) / "vool-home"
            with mock.patch.dict(
                "os.environ",
                {"OPENCLAW_HOME": str(openclaw_home), "VOOL_HOME": str(identity_dir)},
                clear=False,
            ), mock.patch.object(
                onboarding, "data_path", side_effect=lambda name: identity_dir / "data" / name
            ), mock.patch("core.identity_manager.update_local_persona"), mock.patch.object(
                onboarding, "_ensure_voolbook_registration", return_value=None
            ):
                identity = onboarding.ensure_bootstrap_identity()

            self.assertEqual(identity["agent_name"], "VOOL")
            self.assertEqual(config_path.read_text(encoding="utf-8"), original_config)

    def test_ensure_bootstrap_identity_env_hint_wins_over_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity_dir = Path(tmpdir) / "vool-home"
            with mock.patch.dict(
                "os.environ",
                {"VOOL_HOME": str(identity_dir), "VOOL_AGENT_NAME": "EnvName"},
                clear=False,
            ), mock.patch.object(
                onboarding, "data_path", side_effect=lambda name: identity_dir / "data" / name
            ), mock.patch("core.identity_manager.update_local_persona"), mock.patch.object(
                onboarding, "_ensure_voolbook_registration", return_value=None
            ):
                identity = onboarding.ensure_bootstrap_identity()

            self.assertEqual(identity["agent_name"], "EnvName")

    def test_ensure_bootstrap_identity_ignores_malformed_openclaw_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_home = Path(tmpdir) / ".openclaw"
            openclaw_home.mkdir(parents=True, exist_ok=True)
            malformed = "{not json at all"
            (openclaw_home / "openclaw.json").write_text(malformed, encoding="utf-8")
            identity_dir = Path(tmpdir) / "vool-home"

            with mock.patch.dict(
                "os.environ",
                {"OPENCLAW_HOME": str(openclaw_home), "VOOL_HOME": str(identity_dir)},
                clear=False,
            ), mock.patch.object(
                onboarding, "data_path", side_effect=lambda name: identity_dir / "data" / name
            ), mock.patch("core.identity_manager.update_local_persona"), mock.patch.object(
                onboarding, "_ensure_voolbook_registration", return_value=None
            ):
                identity = onboarding.ensure_bootstrap_identity()

            self.assertEqual(identity["agent_name"], "VOOL")
            self.assertEqual((openclaw_home / "openclaw.json").read_text(encoding="utf-8"), malformed)


if __name__ == "__main__":
    unittest.main()
