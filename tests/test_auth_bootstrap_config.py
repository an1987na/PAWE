from pawe_api.config import Settings


def test_bootstrap_credentials_load_from_env_file_fields() -> None:
    settings = Settings(
        _env_file=None,
        bootstrap_admin_username="local-admin",
        bootstrap_admin_password="local-password",
    )

    assert settings.bootstrap_admin_username == "local-admin"
    assert settings.bootstrap_admin_password == "local-password"
