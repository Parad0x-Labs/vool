from unittest.mock import patch

from apps.vool_daemon import DaemonConfig, VoolDaemon
from network.pow_hashcash import verify_pow
from network.transport import TransportRuntime


def test_daemon_boot():
    print("Starting Daemon Phase 30 Boot verification...")
    config = DaemonConfig(
        bind_host="0.0.0.0",
        bind_port=49152,
        advertise_host="127.0.0.1",
        capabilities=["benchmark", "research"],
        compute_class="gpu_elite",
        supported_models=["llama_v3_8b_hash"]
    )

    daemon = VoolDaemon(config=config)

    # Run the daemon with transport mocked so the boot verification does not depend on a real socket bind.
    with patch("network.transport.UDPTransportServer.start", return_value=TransportRuntime("127.0.0.1", 49152, "127.0.0.1", 49152, True)):
        runtime = daemon.start()

    print(f"STUN Runtime bindings: local=[{runtime.host}:{runtime.port}] public=[{runtime.public_host}:{runtime.public_port}]")
    print(f"Local Capability Ad Genesis Nonce: {daemon.local_capability_ad.genesis_nonce}")

    # Assert things started correctly
    assert runtime.running
    if runtime.public_host == runtime.host:
        print("Note: STUN bound to localhost (expected if no STUN internet connection or STUN blocked).")
    else:
        print("SUCCESS: STUN Pierced NAT successfully!")

    assert verify_pow(daemon.local_capability_ad.agent_id, daemon.local_capability_ad.genesis_nonce, 4), "PoW was invalid!"
    print("SUCCESS: Genesis PoW valid.")
    assert daemon.local_capability_ad.compute_class == "gpu_elite"
    assert "llama_v3_8b_hash" in daemon.local_capability_ad.supported_models

    print("\nPhase 30 Integration passed gracefully.")
    daemon.stop()

if __name__ == "__main__":
    test_daemon_boot()
