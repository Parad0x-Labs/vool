from __future__ import annotations

import unittest

from sandbox.network_guard import command_uses_network, parse_command


class NetworkGuardTests(unittest.TestCase):
    def test_blocks_known_network_binaries(self) -> None:
        self.assertTrue(command_uses_network(["curl", "https://example.com"]))
        self.assertTrue(command_uses_network(["/usr/bin/wget", "https://example.com"]))

    def test_detects_network_usage_inside_interpreter_commands(self) -> None:
        argv = parse_command("python3 -c \"import requests; requests.get('https://example.com')\"")
        self.assertTrue(command_uses_network(argv))

    def test_env_wrapped_network_binary_is_detected(self) -> None:
        argv = ["/usr/bin/env", "curl", "https://example.com"]
        self.assertTrue(command_uses_network(argv))

    def test_detects_package_manager_network_actions(self) -> None:
        self.assertTrue(command_uses_network(parse_command("git pull")))
        self.assertTrue(command_uses_network(parse_command("npm install")))
        self.assertTrue(command_uses_network(parse_command("env NODE_ENV=test pnpm add vite")))

    def test_non_network_command_passes(self) -> None:
        self.assertFalse(command_uses_network(["python3", "-c", "print('ok')"]))

    def test_global_flag_before_subcommand_does_not_evade_detection(self) -> None:
        # Regression: a global option that consumes a value (e.g. "-C <dir>",
        # "--prefix <dir>", "--cwd <dir>") used to push the real network
        # subcommand past the inspected window, letting it slip through.
        self.assertTrue(command_uses_network(parse_command("npm --prefix /tmp install left-pad")))
        self.assertTrue(command_uses_network(parse_command("yarn --cwd /tmp add vite")))
        self.assertTrue(command_uses_network(parse_command("git -C /tmp pull")))
        self.assertTrue(command_uses_network(parse_command("pnpm --dir /tmp install")))
        self.assertTrue(command_uses_network(parse_command("cargo -Z unstable install ripgrep")))

    def test_global_flag_with_equals_value_does_not_evade_detection(self) -> None:
        self.assertTrue(command_uses_network(parse_command("git --git-dir=/tmp/.git fetch")))

    def test_global_flag_before_non_network_subcommand_still_passes(self) -> None:
        # Stripping the flags must not turn a local subcommand into a network one.
        self.assertFalse(command_uses_network(parse_command("git -C /tmp status")))
        self.assertFalse(command_uses_network(parse_command("npm --prefix /tmp run build")))
        self.assertFalse(command_uses_network(parse_command("yarn --cwd /tmp build")))


if __name__ == "__main__":
    unittest.main()
