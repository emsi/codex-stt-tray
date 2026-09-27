import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """No test ever reads the user's auth file or starts their Codex binary."""
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_CLI_PATH", str(tmp_path / "missing-codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return home
