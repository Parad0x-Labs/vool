"""The timing wrapper must survive a globally patched subprocess.run mid-session."""
import subprocess


def test_patched_subprocess_never_kills_the_timing_flush(monkeypatch, tmp_path):
    import ops.pytest_timing as timing

    # Reset the cache so this test controls the first _source_identity call.
    monkeypatch.setattr(timing, "_SOURCE_IDENTITY_CACHE", None)

    def hostile(*args, **kwargs):
        raise AssertionError("no subprocess may run while the bridge patch is active")

    monkeypatch.setattr(subprocess, "run", hostile)
    # This is exactly what killed CI run 36294291571 shard tests(9): a flush inside
    # the patched window. It must degrade to unknown identity, never raise.
    identity = timing._source_identity()
    assert identity == {"git_head_sha": None, "git_dirty": None, "git_changed_paths": None}, identity


def test_identity_is_cached_after_first_success(monkeypatch):
    import ops.pytest_timing as timing

    cached = {"git_head_sha": "deadbeef", "git_dirty": False, "git_changed_paths": 0}
    monkeypatch.setattr(timing, "_SOURCE_IDENTITY_CACHE", cached)
    calls = []

    def counting(*args, **kwargs):
        calls.append(args)
        raise AssertionError("cache must prevent any subprocess spawn")

    import subprocess as _sp

    real_run = _sp.run
    monkeypatch.setattr(_sp, "run", counting)
    assert timing._source_identity() is cached
    assert calls == []
    _ = real_run  # keep the reference honest
