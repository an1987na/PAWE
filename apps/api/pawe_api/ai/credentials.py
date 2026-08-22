import base64
import os
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.config import Settings, get_settings
from pawe_api.db import models


class AICredentialError(ValueError):
    pass


class AICredentialCipher:
    def __init__(self, settings: Settings | None = None) -> None:
        resolved = settings or get_settings()
        secret = resolved.ai_credential_encryption_key
        if not secret and resolved.env != "production":
            # Local-only fallback keeps credentials decryptable across restarts.
            secret = f"pawe-development-ai-credential:{resolved.database_url}"
        if not secret:
            raise AICredentialError(
                "PAWE_AI_CREDENTIAL_ENCRYPTION_KEY must be configured before saving credentials"
            )
        self._key = sha256(secret.encode("utf-8")).digest()

    def encrypt(self, api_key: str) -> str:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, api_key.encode("utf-8"), b"pawe-ai-key-v1")
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            payload = base64.urlsafe_b64decode(value.encode("ascii"))
            return AESGCM(self._key).decrypt(
                payload[:12], payload[12:], b"pawe-ai-key-v1"
            ).decode("utf-8")
        except Exception as exc:
            raise AICredentialError("Saved AI credential cannot be decrypted") from exc


def normalize_api_key(value: str) -> str:
    api_key = value.strip()
    if len(api_key) < 20 or len(api_key) > 512 or any(character.isspace() for character in api_key):
        raise AICredentialError("API key format is invalid")
    return api_key


def key_hint(api_key: str) -> str:
    return f"••••{api_key[-4:]}"


async def get_user_credential(
    session: AsyncSession, user_id: uuid.UUID
) -> models.UserAICredential | None:
    return cast(
        models.UserAICredential | None,
        await session.scalar(
            select(models.UserAICredential).where(models.UserAICredential.user_id == user_id)
        ),
    )


async def save_user_credential(
    session: AsyncSession,
    user_id: uuid.UUID,
    api_key: str,
    model: str,
    settings: Settings | None = None,
) -> models.UserAICredential:
    normalized = normalize_api_key(api_key)
    now = datetime.now(UTC)
    row = await get_user_credential(session, user_id)
    encrypted = AICredentialCipher(settings).encrypt(normalized)
    if row is None:
        row = models.UserAICredential(
            id=uuid.uuid4(),
            user_id=user_id,
            provider="openai",
            encrypted_api_key=encrypted,
            key_hint=key_hint(normalized),
            model=model,
            created_at=now,
            updated_at=now,
            last_used_at=None,
        )
        session.add(row)
    else:
        row.encrypted_api_key = encrypted
        row.key_hint = key_hint(normalized)
        row.model = model
        row.updated_at = now
    await session.commit()
    await session.refresh(row)
    return row


async def delete_user_credential(session: AsyncSession, user_id: uuid.UUID) -> bool:
    row = await get_user_credential(session, user_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def personal_provider_config(
    session: AsyncSession, user_id: uuid.UUID, settings: Settings | None = None
) -> tuple[str, str] | None:
    row = await get_user_credential(session, user_id)
    if row is None:
        return None
    return AICredentialCipher(settings).decrypt(row.encrypted_api_key), row.model
