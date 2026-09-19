def test_fence_probe():
    from tests.gauntlet import harness as H
    H.runtime()
    from core.final_answer_authorship import precall_author_verdict
    from core.model_registry import ModelRegistry
    for m in ModelRegistry().list_manifests():
        v = precall_author_verdict(manifest=m, request_text="what is a python list comprehension?", output_mode="plain_text")
        print("PROBE", m.provider_id, "| eligible:", v.eligible if v else None, "| reason:", str(getattr(v, "reason", ""))[:100] if v else None)
