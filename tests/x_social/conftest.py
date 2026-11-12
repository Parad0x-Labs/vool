"""Screenshots from the served-browser tests go to a temp folder unless SOCIAL_SHOTS names one."""
import pytest


@pytest.fixture(autouse=True)
def _social_shots_folder(monkeypatch, tmp_path_factory):
    import os

    if not os.environ.get("SOCIAL_SHOTS"):
        monkeypatch.setenv("SOCIAL_SHOTS", str(tmp_path_factory.mktemp("social-shots")))
