from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.runtime_paths import project_path


@dataclass(frozen=True)
class FeatureFlag:
    name: str
    state: str
    reason: str


def get_feature_flags() -> list[FeatureFlag]:
    return [
        FeatureFlag("LOCAL_STANDALONE", "implemented", "Vool runs locally without the swarm."),
        FeatureFlag("LIQUEFY_SIDECAR", "optional", "Liquefy integration remains optional and non-blocking."),
        FeatureFlag("SOLANA_SIDECAR", "optional", "Solana anchoring and DNA payment hooks stay optional."),
        FeatureFlag("SIMULATED_PAYMENTS", "simulated", "Credits and DNA payment flow are explicitly non-trustless."),
        FeatureFlag("SIMULATED_DEX", "simulated", "Credit DEX remains disabled for production settlement."),
        FeatureFlag("SIMULATED_DHT", "partial", "DHT exists, but it is not yet hardened as a public routing layer."),
        FeatureFlag("EXPERIMENTAL_WAN", "partial", "WAN readiness exists behind probes, relays, and fallbacks."),
        FeatureFlag("STREAM_TRANSPORT", "implemented", "Large payload transport is split from UDP control-plane."),
        FeatureFlag("KNOWLEDGE_PRESENCE_LAYER", "implemented", "The swarm now tracks live presence, knowledge holders, freshness, and fetch routes."),
        FeatureFlag("HUMAN_INPUT_ADAPTATION", "implemented", "Messy human input is normalized into stable local intent with session-aware references and confidence scoring."),
        FeatureFlag("TIERED_CONTEXT_LOADER", "implemented", "Prompt assembly now uses bootstrap, relevant, and cold context layers with explicit budgets and access logging."),
        FeatureFlag("MODEL_EXECUTION_LAYER", "implemented", "Provider abstraction, memory-first routing, health/failover, output contracts, and candidate-knowledge isolation now exist for local and optional remote model backends."),
        FeatureFlag("BOUNDED_CURIOSITY", "implemented", "A bounded curiosity layer can follow high-signal topics through curated sources, keep outputs candidate-only, and avoid relearning everything from scratch."),
        FeatureFlag("MEDIA_EVIDENCE_INGESTION", "implemented", "External URLs, social posts, images, and video references can now be ingested as evidence with credibility scoring, multimodal review hooks, and candidate-only handling."),
        FeatureFlag("SIGNED_HTTP_WRITES", "implemented", "Meet and Brain Hive HTTP write routes now require signed write envelopes with nonce replay protection and route-to-actor binding."),
        FeatureFlag("KNOWLEDGE_POSSESSION_CHALLENGE", "implemented", "Proof-capable knowledge manifests can now answer CAS chunk possession challenges before a holder claim is trusted."),
        FeatureFlag("RUNTIME_DEPLOYMENT_GUARD", "implemented", "Public meet-node startup now blocks placeholder URLs, placeholder tokens, and unsafe default runtime deployment posture."),
        FeatureFlag("BRAIN_HIVE_COMMONS", "partial", "Agent-only topic, post, claim-link, profile, stats, HTTP API, signed writes, and anti-spam admission now exist, but live deployment proof and moderation depth are still pending."),
        FeatureFlag("USER_MEMORY_SUMMARY", "implemented", "Users can inspect what Vool learned, stored, indexed, and exchanged through a single summary view."),
        FeatureFlag("MOBILE_COMPANION_VIEW", "implemented", "A metadata-first mobile companion snapshot now exists over the user summary layer."),
        FeatureFlag("CAS_STORAGE", "implemented", "Chunked content-addressed storage is available locally."),
        FeatureFlag("EVENT_HASH_CHAIN", "implemented", "Critical local events are chained for tamper evidence."),
        FeatureFlag("OVERNIGHT_SOAK_TOOLING", "implemented", "Overnight readiness and morning-after audit reports now exist for real local soak gating."),
        FeatureFlag("CHANNEL_GATEWAY_SCAFFOLD", "partial", "A platform-neutral channel gateway now normalizes Telegram, Discord, and web-companion access into one agent path, but live surface wiring and proof are still pending."),
        FeatureFlag("MEET_AND_GREET_SERVER", "partial", "The meet-and-greet contract, service layer, HTTP scaffold, signed writes, and challenge routes exist, but deployment topology and live redundancy proof are still pending."),
        FeatureFlag("MEET_CLUSTER_REPLICATION", "partial", "Pull-based snapshot and delta replication exist for meet nodes, but global convergence is not yet proven across live regions."),
        FeatureFlag(
            "OPENCLAW_INTEGRATION_READY",
            "partial",
            f"Integration target retained via optional sidecars; standalone mode remains valid. Reference: {project_path('core', 'dna_payment_bridge.py')}",
        ),
    ]


def flag_map() -> dict[str, FeatureFlag]:
    return {flag.name: flag for flag in get_feature_flags()}


def iter_feature_rows() -> Iterable[tuple[str, str, str]]:
    for flag in get_feature_flags():
        yield flag.name, flag.state, flag.reason
