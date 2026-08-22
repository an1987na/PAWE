from argon2 import PasswordHasher
from pawe_api.auth.security import PasswordManager, new_secret, secret_hash


def test_passwords_use_salted_argon2_hashes() -> None:
    manager = PasswordManager(PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1))
    first = manager.hash("a sufficiently long password")
    second = manager.hash("a sufficiently long password")

    assert first.startswith("$argon2id$")
    assert first != second
    assert manager.verify(first, "a sufficiently long password")
    assert not manager.verify(first, "wrong password")


def test_session_secrets_are_random_and_stored_as_fixed_hashes() -> None:
    first = new_secret()
    second = new_secret()

    assert first != second
    assert len(secret_hash(first)) == 64
    assert first not in secret_hash(first)
