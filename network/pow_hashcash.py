import hashlib
import os
import time

from core import audit_logger, policy_engine


def _compute_hash(identity_string: str, nonce: str) -> str:
    """Computes a SHA-256 hash using the identity string and the nonce."""
    payload = f"{identity_string}:{nonce}".encode()
    return hashlib.sha256(payload).hexdigest()


def required_pow_difficulty(default: int = 4) -> int:
    raw = os.environ.get("VOOL_POW_DIFFICULTY")
    if raw:
        try:
            return max(1, min(8, int(raw)))
        except ValueError:
            pass
    policy_value = policy_engine.get("network.pow_min_difficulty", default)
    try:
        return max(1, min(8, int(policy_value)))
    except (TypeError, ValueError):
        return default

def generate_pow(identity_string: str, target_difficulty: int = 4) -> str:
    """
    Phase 30: Sybil Resistance at Genesis
    Spins the CPU until it finds a nonce that hashes (with the ip_address)
    to a digest starting with `target_difficulty` number of leading zeros.
    """
    target_prefix = "0" * target_difficulty
    nonce_counter = 0
    start_time = time.time()

    audit_logger.log(
        "pow_generation_started",
        target_id=identity_string,
        target_type="sybil_resistance",
        details={"difficulty": target_difficulty}
    )

    while True:
        nonce = str(nonce_counter)
        digest = _compute_hash(identity_string, nonce)
        if digest.startswith(target_prefix):
            duration = time.time() - start_time
            audit_logger.log(
                "pow_generation_success",
                target_id=identity_string,
                target_type="sybil_resistance",
                details={
                    "nonce": nonce,
                    "hash": digest,
                    "duration_seconds": round(duration, 3),
                    "attempts": nonce_counter
                }
            )
            return nonce

        nonce_counter += 1

        # Failsafe so the node doesn't melt on extreme difficulty tests (hard limit ~2m iterations)
        if nonce_counter > 5_000_000:
            audit_logger.log(
                "pow_generation_failed",
                target_id=identity_string,
                target_type="sybil_resistance",
                details={"reason": "max_iterations_reached"}
            )
            return ""

def verify_pow(identity_string: str, nonce: str, target_difficulty: int = 4) -> bool:
    """
    Checks if the provided nonce solves the PoW puzzle for the given identity string.
    """
    if not nonce:
        return False

    target_prefix = "0" * target_difficulty
    digest = _compute_hash(identity_string, nonce)
    return digest.startswith(target_prefix)
