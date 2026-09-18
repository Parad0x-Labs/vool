from __future__ import annotations

from core.media_providers import (
    MediaProvider,
    extract_media_ref,
    get_media_provider,
    provider_names,
    register_media_provider,
)


def test_builtin_provider_request_shapes() -> None:
    g = get_media_provider("generic")
    assert g.headers("k")["Authorization"] == "Bearer k" and g.body("hi") == {"prompt": "hi"}
    rp = get_media_provider("runpod")
    assert rp.headers("k")["Authorization"] == "Bearer k" and rp.body("hi") == {"input": {"prompt": "hi"}}
    rep = get_media_provider("replicate")
    assert rep.headers("k")["Authorization"] == "Token k"
    assert rep.body("hi", {"version": "v1", "width": 512}) == {"input": {"prompt": "hi", "width": 512}, "version": "v1"}
    fal = get_media_provider("fal")
    assert fal.headers("k")["Authorization"] == "Key k" and fal.body("hi") == {"prompt": "hi"}


def test_unknown_provider_falls_back_to_generic() -> None:
    assert get_media_provider("does-not-exist").name == "generic"
    assert get_media_provider("").name == "generic"


def test_register_custom_provider() -> None:
    register_media_provider(MediaProvider("myco", "Bearer", wrap_input=True))
    assert "myco" in provider_names()
    p = get_media_provider("MYCO")           # case-insensitive
    assert p.wrap_input is True and p.body("x") == {"input": {"prompt": "x"}}


def test_extract_media_ref_across_shapes() -> None:
    assert extract_media_ref({"url": "http://x/y.png"}) == "http://x/y.png"
    assert extract_media_ref({"output": ["http://a/v.mp4", "http://b"]}) == "http://a/v.mp4"
    assert extract_media_ref({"images": [{"url": "http://i.png"}]}) == "http://i.png"       # nested list
    assert extract_media_ref({"video": {"url": "http://v.mp4"}}) == "http://v.mp4"          # nested dict
    assert extract_media_ref({"urls": {"get": "http://poll"}}) == "http://poll"             # async poll url
    assert extract_media_ref({"output": "job-123"}) == "job-123"                            # keyed value as-is
    assert extract_media_ref({"nothing": 1}) == ""
    assert extract_media_ref("http://plain") == "http://plain"
