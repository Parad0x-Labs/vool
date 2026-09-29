from __future__ import annotations

import unittest
import uuid
from unittest import mock

from apps.vool_agent import VoolAgent
from core.media_analysis_pipeline import MediaAnalysisPipeline
from core.media_ingestion import build_multimodal_attachments, ingest_media_evidence
from core.social_source_policy import evaluate_social_source
from storage.db import get_connection
from storage.migrations import run_migrations


class MediaIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in ("media_evidence_log", "candidate_knowledge_lane", "model_provider_manifests", "local_tasks"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()

    def test_social_post_is_low_trust_orientation_only(self) -> None:
        verdict = evaluate_social_source("x.com")
        self.assertTrue(verdict.allowed_for_orientation)
        self.assertFalse(verdict.allowed_as_primary_evidence)
        self.assertLess(verdict.credibility_score, 0.25)

    def test_ingest_media_evidence_from_source_context(self) -> None:
        items = ingest_media_evidence(
            task_id=f"task-{uuid.uuid4().hex}",
            trace_id=f"trace-{uuid.uuid4().hex}",
            user_input="check this",
            source_context={
                "external_evidence": [
                    {"kind": "social_post", "url": "https://x.com/example/status/1", "text": "breaking thing happened"},
                    {"kind": "image", "url": "https://example.com/image.png", "caption": "Screenshot of setup"},
                    {"kind": "video", "url": "https://youtube.com/watch?v=abc", "transcript": "Video says the setup fails on boot."},
                ]
            },
        )
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["source_kind"], "social")
        self.assertTrue(any(item["media_kind"] == "image" for item in items))
        self.assertTrue(any(item["media_kind"] == "video" for item in items))

    def test_multimodal_attachments_only_include_media(self) -> None:
        evidence = [
            {"media_kind": "social_post", "reference": "https://x.com/1", "blocked": False},
            {"media_kind": "image", "reference": "https://example.com/a.png", "blocked": False, "caption": "photo"},
            {"media_kind": "video", "reference": "https://example.com/v.mp4", "blocked": False, "transcript": "hello"},
        ]
        attachments = build_multimodal_attachments(evidence)
        self.assertEqual(len(attachments), 2)
        self.assertEqual({item["kind"] for item in attachments}, {"image", "video"})

    def test_media_pipeline_uses_multimodal_provider_when_needed(self) -> None:
        from core.model_registry import ModelRegistry

        registry = ModelRegistry()
        registry.register_manifest(
            {
                "provider_name": "local-mm",
                "model_name": "vision-helper",
                "source_type": "http",
                "adapter_type": "openai_compatible",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["summarize", "multimodal"],
                "runtime_config": {"base_url": "http://127.0.0.1:1234"},
                "enabled": True,
            }
        )
        pipeline = MediaAnalysisPipeline(registry)
        evidence = [
            {
                "media_kind": "image",
                "reference": "https://example.com/image.png",
                "source_domain": "example.com",
                "credibility": {"score": 0.45},
                "social_policy": {"reason": "Unknown source"},
                "blocked": False,
                "requires_multimodal": True,
                "caption": "",
                "transcript": "",
            }
        ]
        with mock.patch("adapters.openai_compatible_adapter.requests.post") as post, mock.patch(
            "adapters.openai_compatible_adapter.requests.get"
        ) as get:
            get.return_value.status_code = 200
            get.return_value.raise_for_status.return_value = None
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {
                "choices": [{"message": {"content": "The image appears to show a setup screenshot with a bot configuration panel."}}],
                "usage": {},
            }
            result = pipeline.analyze(task_id=f"task-{uuid.uuid4().hex}", task_summary="check image", evidence_items=evidence)
        self.assertTrue(result.used_provider)
        self.assertTrue(result.candidate_id)

    def test_agent_run_once_surfaces_media_analysis(self) -> None:
        agent = VoolAgent(backend_name="local", device="media-test")
        agent.start()
        with mock.patch.object(agent.media_pipeline, "analyze") as analyze:
            analyze.return_value.used_provider = False
            analyze.return_value.provider_id = None
            analyze.return_value.candidate_id = None
            analyze.return_value.analysis_text = ""
            analyze.return_value.reason = "textual_media_only"
            analyze.return_value.evidence_items = []
            result = agent.run_once(
                "check this post and screenshot",
                source_context={
                    "external_evidence": [
                        {"kind": "social_post", "url": "https://x.com/example/status/1", "text": "status text"},
                        {"kind": "image", "url": "https://example.com/image.png", "caption": "setup screenshot"},
                    ]
                },
            )
        self.assertIn("media_analysis", result)
        self.assertIn("reason", result["media_analysis"])

    def test_media_evidence_is_logged(self) -> None:
        ingest_media_evidence(
            task_id=f"task-{uuid.uuid4().hex}",
            trace_id=f"trace-{uuid.uuid4().hex}",
            user_input="https://x.com/example/status/1",
            source_context={},
        )
        conn = get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) AS c FROM media_evidence_log").fetchone()["c"]
        finally:
            conn.close()
        self.assertGreaterEqual(count, 1)

    def test_substring_classification_cannot_steer_domain_policy(self) -> None:
        """A URL whose PATH carries a platform token ("x.com/", "youtube.com/") or whose host is
        a lookalike ("x.com.evil.com") is LABELLED social/video by substring inference -- but the
        policy columns must follow the parsed HOST, never the label: domain, credibility,
        social_policy and blocked are computed from the real host, so a spoofed label can neither
        borrow a platform's trust nor dodge a blocked host's refusal."""
        import core.media_ingestion as mi

        spoofed = [
            # (url, the host the URL actually names -- the only thing policy may read)
            ("https://evil-actor.example/x.com/status/1", "evil-actor.example"),
            ("https://x.com.evil-actor.example/status/1", "x.com.evil-actor.example"),
            ("https://evil-actor.example/youtube.com/watch?v=1", "evil-actor.example"),
            ("https://evil-actor.example/story.png", "evil-actor.example"),
        ]
        for url, real_host in spoofed:
            with self.subTest(url=url):
                evidence = mi._normalize_item({"url": url, "text": "body"})
                self.assertIsNotNone(evidence)
                self.assertEqual(evidence.source_domain, real_host, url)
                # the label may say social/video; the trust rows must be the real host's
                self.assertEqual(
                    evidence.credibility, mi.evaluate_source_domain(real_host).to_dict(), url
                )
                self.assertEqual(
                    evidence.social_policy, mi.evaluate_social_source(real_host).to_dict(), url
                )
                self.assertFalse(evidence.blocked, "a neutral host is not blocked by its label")

    def test_blocked_evidence_reaches_no_model_surface(self) -> None:
        import core.media_ingestion as mi

        blocked_item = {
            "blocked": True, "source_domain": "blocked.example",
            "credibility": {"score": 0.0}, "social_policy": {}, "media_kind": "text",
            "text": "secret payload", "caption": "", "transcript": "",
            "reference": "https://blocked.example/x",
        }
        self.assertEqual(mi.build_media_context_snippets([blocked_item]), [])
        self.assertEqual(mi.build_multimodal_attachments([blocked_item]), [])

    def test_non_web_reference_is_not_a_page_fetch(self) -> None:
        """A reference without an http(s) scheme classifies as plain text and MUST NOT be handed
        to a page transport: the reproducer for the door's scheme gap (a file: URL whose target
        bytes came back as evidence text) is pinned here at the ingestion owner."""
        import core.media_ingestion as mi

        calls: list[str] = []

        def _no_fetch(url, **kwargs):
            calls.append(f"http:{url}")
            return {"status": "ok", "text": "SHOULD NEVER APPEAR", "html": "", "final_url": url}

        def _no_render(url, **kwargs):
            calls.append(f"browser:{url}")
            return {"status": "disabled_by_policy", "final_url": url}

        with mock.patch.object(mi, "http_fetch_text", _no_fetch), mock.patch.object(
            mi, "browser_render", _no_render
        ):
            out = mi._fetch_reference_text("file:///etc/hosts")
        self.assertEqual(out["status"], "not_a_web_reference")
        self.assertEqual(out["text"], "")
        self.assertFalse(out["used_browser"])
        self.assertEqual(calls, [])

        evidence = mi._normalize_item({"url": "file:///etc/hosts"}, fetch_text_reference=True)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.text, "")
        self.assertEqual(evidence.metadata.get("fetch_status"), "not_a_web_reference")
        self.assertNotIn("SHOULD NEVER APPEAR", repr(evidence.to_dict()))
if __name__ == "__main__":
    unittest.main()
