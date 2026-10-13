"""Owned daemon fixture: pin host inputs for a scripted provider, retain the real load gate.

Production launches never import this module or consult a resource-override environment flag.
"""
from __future__ import annotations

import argparse
import sys

from tests.provider_fixture_resources_20261007 import fixture_host_snapshot


def configure_fixture_host(available_gb: float) -> None:
    from core import resource_governor, system_resources

    system_resources.snapshot = lambda **_kwargs: fixture_host_snapshot(available_gb)
    resource_governor.resident_models = lambda **_kwargs: []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--available-gb", type=float, required=True)
    inputs, server_args = parser.parse_known_args()
    configure_fixture_host(inputs.available_gb)
    sys.argv = ["vool-api-server", *server_args]
    from apps.vool_api_server import main as serve

    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
