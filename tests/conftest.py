from pathlib import Path

import pytest

from simpleagent.config import Config


@pytest.fixture
def sa_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录，避免测试写到真实的 ~/.simpleagent。"""
    home = tmp_path / "sa_home"
    monkeypatch.setenv("SIMPLEAGENT_HOME", str(home))
    return home


@pytest.fixture
def config(sa_home: Path) -> Config:
    return Config.model_validate(
        {
            "default_profile": "a",
            "profiles": {
                "a": {"base_url": "http://a.invalid/v1", "model": "model-a"},
                "b": {"base_url": "http://b.invalid/v1", "model": "model-b"},
            },
        }
    )
