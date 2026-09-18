from datetime import datetime

from storage.db import execute_query


class PeerManager:
    """
    Manages explicit Peer Trust calculation and tracking based on V2 Math.
    """

    @staticmethod
    def _clamp(val: float, min_val: float, max_val: float) -> float:
        return max(min_val, min(val, max_val))

    @staticmethod
    def get_peer(peer_id: str) -> dict:
        r = execute_query("SELECT * FROM peers WHERE peer_id = ?", (peer_id,))
        return r[0] if r else None

    @staticmethod
    def mark_seen(peer_id: str) -> None:
        """Ensure peer exists and updates last seen."""
        now = datetime.now().isoformat()
        r = PeerManager.get_peer(peer_id)
        if not r:
            from core import policy_engine
            initial_trust = policy_engine.get("trust.initial_peer_trust", 0.5)
            execute_query("""
                INSERT INTO peers (peer_id, display_alias, trust_score, created_at, updated_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (peer_id, "Unknown Node", initial_trust, now, now, now))
        else:
            execute_query("UPDATE peers SET last_seen_at = ?, updated_at = ? WHERE peer_id = ?", (now, now, peer_id))

    @staticmethod
    def update_peer_trust(peer_id: str, delta: float = 0.0) -> None:
        """
        Recalculates the exact V2 peer trust formula based on DB stats.
        Optionally allows arbitrary deltas (like striking).
        """
        peer = PeerManager.get_peer(peer_id)
        if not peer: return

        # peer_trust_next = clamp(peer_trust_current + (successful_shards * 0.02) - (failed_shards * 0.04) - (strike_count * 0.10), 0.0, 1.0)

        current = float(peer["trust_score"])
        s_shards = int(peer["successful_shards"])
        f_shards = int(peer["failed_shards"])
        strikes = int(peer["strike_count"])

        # We always calculate from the base (current) + recent shard impact + delta
        new_trust = PeerManager._clamp(
            current
            + (s_shards * 0.02)
            - (f_shards * 0.04)
            - (strikes * 0.10)
            + delta,
            0.0, 1.0
        )

        execute_query("UPDATE peers SET trust_score = ?, updated_at = ? WHERE peer_id = ?", (new_trust, datetime.now().isoformat(), peer_id))
