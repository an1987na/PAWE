from pathlib import Path

import pytest

from scripts import native_dev


def test_native_tests_keep_database_user_and_remove_inherited_production_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "native.env"
    config.write_text(
        "PAWE_DATABASE_URL=postgresql+asyncpg://pawe_native:fake@127.0.0.1:55432/pawe_native\n"
    )
    monkeypatch.setattr(native_dev, "ENV_FILE", config)
    monkeypatch.setenv("PAWE_OPENAI_API_KEY", "sk-inherited-not-permitted")
    monkeypatch.setenv("PAWE_AI_ENABLED", "true")
    env = native_dev.environment(testing=True)
    assert (
        env["PAWE_DATABASE_URL"]
        == "postgresql+asyncpg://pawe_native:fake@127.0.0.1:55432/pawe_native_test"
    )
    assert env["PAWE_OPENAI_API_KEY"] == ""
    assert env["PAWE_AI_ENABLED"] == "false"
    assert env["PAWE_ENV_FILE"] == str(config)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://user:fake@192.168.2.1:5432/pawe",
        "postgresql+asyncpg://pawe_native:fake@127.0.0.1:55432/pawe_native_other",
        "postgresql+asyncpg://pawe_native:fake@127.0.0.1:55432/pawe_native?host=192.168.2.1",
    ],
)
def test_native_refuses_remote_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    config = tmp_path / "native.env"
    config.write_text(f"PAWE_DATABASE_URL={url}\n")
    monkeypatch.setattr(native_dev, "ENV_FILE", config)
    with pytest.raises(SystemExit, match="Refusing"):
        native_dev.environment()
