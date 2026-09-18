import sys
import time

from network.pow_hashcash import generate_pow, verify_pow
from network.signer import get_local_peer_id


def test_sybil_defense():
    print("Beginning Sybil Defense Genesis PoW Test...")
    peer_id = get_local_peer_id()

    start = time.time()
    nonce = generate_pow(peer_id, target_difficulty=4)
    duration = time.time() - start

    print(f"Generated PoW nonce: {nonce} in {duration:.3f} seconds for identity: {peer_id[:8]}...")

    if not nonce:
        print("FAILED: Did not generate a nonce.")
        sys.exit(1)

    is_valid = verify_pow(peer_id, nonce, target_difficulty=4)
    print(f"Nonce Validated: {is_valid}")
    if not is_valid:
        print("FAILED: Nonce was rejected by verify_pow.")
        sys.exit(1)

    print("Testing malicious capability ad (wrong nonce)...")
    is_malicious_valid = verify_pow(peer_id, "9999999", target_difficulty=4)
    if is_malicious_valid:
        print("FAILED: Allowed an invalid nonce!")
        sys.exit(1)

    print("\nSUCCESS: Sybil PoW Defense passed.")

if __name__ == "__main__":
    test_sybil_defense()
