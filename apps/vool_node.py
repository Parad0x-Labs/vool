
from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)# Repo-root bootstrap: allow running as a file (python3 apps/<x>.py), not just -m.
import os as _bootstrap_os
import sys as _bootstrap_sys
from dataclasses import dataclass

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)


from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)
from network import quarantine, rate_limiter
from network.protocol import Protocol


@dataclass
class NodeRuntime:
    host: str
    port: int
    running: bool

class VoolNode:
    """
    V2: The strict P2P Daemon.
    Expects incoming requests, validates envelopes via `network.protocol`,
    and checks rate limits before ever processing a payload.
    """
    def __init__(self, host="0.0.0.0", port=49152):
        self.host = host
        self.port = port
        self.running = False

    def start(self) -> NodeRuntime:
        # Bind socket / start loop (Mocked for V2 safety until network testing)
        self.running = True
        print(f"[NODE] Vool P2P Daemon listening on {self.host}:{self.port} (V2 Protocol Strict Envelope)")
        # Real implementation would use selected python sockets/asyncio
        return NodeRuntime(
            host=self.host,
            port=self.port,
            running=True,
        )

    def handle_message(self, raw_bytes: bytes, client_address: tuple):
        """
        The absolute security gate for the external world.
        """
        try:
            # 1. Enforce strict JSON envelope
            envelope = Protocol.decode_and_validate(raw_bytes)
            sender_id = envelope["sender_peer_id"]

            # 2. Check Quarantine List
            if quarantine.is_peer_quarantined(sender_id):
                print(f"[NODE] Ignored msg from quarantined peer: {sender_id}")
                return

            # 3. Rate Limiter check
            if not rate_limiter.allow(sender_id):
                print(f"[NODE] Rate limit exceeded by peer: {sender_id}")
                return

            # 4. Dispatch by msg_type
            msg_type = envelope["msg_type"]
            print(f"[NODE] Accepted safe envelope: {msg_type} from {sender_id}")

            # Process Payload here...

        except ValueError as e:
            # Log strike against peer (if possible to extract ID safely)
            print(f"[NODE] Dropped inbound packet. Protocol Error: {e}")
        except Exception as e:
            print(f"[NODE] Fatal inbound packet failure: {e}")

if __name__ == "__main__":
    # C15: the ONE bounded unattended preflight before node bootstrap reaches a credential API.
    from core.unattended_preflight import preflight

    preflight("apps.vool_node")
    node = VoolNode()
    node.start()
