from pathlib import Path

import pytest

from simpleagent.cli import main
from simpleagent.config import (
    Config,
    ConfigError,
    Profile,
    config_path,
    init_config,
    load_config,
    read_env_file,
)


def test_init_writes_example_config_that_loads(sa_home: Path):
    path = init_config()
    assert path == sa_home / "config.toml"

    config = load_config()
    assert config.default_profile == "deepseek"
    assert config.profiles["deepseek"].quirks.reasoning_echo == "current_turn"
    assert config.profiles["glm"].extra_body == {"thinking": {"type": "enabled"}}
    assert config.profiles["local"].api_key_env is None
    assert config.profiles["local"].quirks.reasoning_field == "reasoning"


def test_init_refuses_to_overwrite(sa_home: Path):
    init_config()
    with pytest.raises(ConfigError, match="已存在"):
        init_config()


def test_missing_config_hints_init(sa_home: Path):
    with pytest.raises(ConfigError, match="sa init"):
        load_config()


def _write(content: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_default_profile_must_exist(sa_home: Path):
    _write('default_profile = "x"\n[profiles.a]\nbase_url = "http://a"\nmodel = "m"\n')
    with pytest.raises(ConfigError, match="default_profile"):
        load_config()


def test_unknown_keys_are_rejected(sa_home: Path):
    # 拼错的配置项要报错，而不是被悄悄忽略
    _write('default_profile = "a"\n[profiles.a]\nbase_url = "http://a"\nmodel = "m"\nmodle = "x"\n')
    with pytest.raises(ConfigError, match="modle"):
        load_config()


def test_api_key_from_env(sa_home: Path, monkeypatch: pytest.MonkeyPatch):
    profile = Profile(base_url="http://a", model="m", api_key_env="SA_TEST_KEY")
    monkeypatch.delenv("SA_TEST_KEY", raising=False)
    with pytest.raises(ConfigError, match="SA_TEST_KEY"):
        profile.api_key()
    monkeypatch.setenv("SA_TEST_KEY", "sk-test")
    assert profile.api_key() == "sk-test"
    assert Profile(base_url="http://a", model="m").api_key() == "not-needed"


def test_api_key_from_env_file(sa_home: Path, monkeypatch: pytest.MonkeyPatch):
    sa_home.mkdir(parents=True)
    (sa_home / ".env").write_text(
        "# 注释\n\nSA_TEST_KEY=sk-from-file\nexport QUOTED='sk-quoted'\nBAD LINE\n",
        encoding="utf-8",
    )
    assert read_env_file() == {"SA_TEST_KEY": "sk-from-file", "QUOTED": "sk-quoted"}

    monkeypatch.delenv("SA_TEST_KEY", raising=False)
    profile = Profile(base_url="http://a", model="m", api_key_env="SA_TEST_KEY")
    assert profile.api_key() == "sk-from-file"
    monkeypatch.setenv("SA_TEST_KEY", "sk-from-env")
    assert profile.api_key() == "sk-from-env"  # 环境变量优先
    assert "SA_TEST_KEY" not in read_env_file(sa_home / "missing.env")


def test_key_pasted_into_api_key_env_is_not_leaked(sa_home: Path):
    secret = "sk-aaaabbbbccccdddd"
    _write(
        'default_profile = "a"\n[profiles.a]\nbase_url = "http://a"\nmodel = "m"\n'
        f'api_key_env = "{secret}"\n'
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    message = str(excinfo.value)
    assert "profiles.a.api_key_env" in message
    assert ".env" in message
    assert secret not in message


def test_cli_init_and_config_error(sa_home: Path, capsys: pytest.CaptureFixture[str]):
    assert main([]) == 1
    assert "sa init" in capsys.readouterr().err
    assert main(["init"]) == 0
    assert (sa_home / "config.toml").exists()


def test_cli_version(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    name, _, number = capsys.readouterr().out.strip().partition(" ")
    assert name == "simpleagent" and number[0].isdigit()


def test_api_key_env_names_collects_all_profiles():
    config = Config.model_validate(
        {
            "default_profile": "a",
            "profiles": {
                "a": {"base_url": "http://a", "model": "m", "api_key_env": "A_KEY"},
                "b": {"base_url": "http://b", "model": "m", "api_key_env": "B_KEY"},
                "local": {"base_url": "http://localhost", "model": "m"},
            },
        }
    )
    assert config.api_key_env_names() == frozenset({"A_KEY", "B_KEY"})


def test_panel_config(sa_home: Path):
    base = 'default_profile = "a"\n[profiles.a]\nbase_url = "http://a"\nmodel = "m"\n'
    _write(base)
    assert load_config().panel.archive_after_minutes == 30  # 不写就是 30 分钟
    _write(base + "[panel]\narchive_after_minutes = 5\n")
    assert load_config().panel.archive_after_minutes == 5
    _write(base + "[panel]\narchive_after_minutes = 0\n")
    with pytest.raises(ConfigError, match="archive_after_minutes"):
        load_config()
