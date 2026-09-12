from cryptography.fernet import Fernet, InvalidToken

from app.shopify.config import settings


def _fernet() -> Fernet:
    key = (settings.token_encryption_key or "").strip()

    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not configured")

    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY is invalid"
        ) from exc


def encrypt_token(token: str) -> str:
    token = (token or "").strip()

    if not token:
        raise ValueError("Token cannot be empty")

    return _fernet().encrypt(
        token.encode("utf-8")
    ).decode("utf-8")


def decrypt_token(encrypted_token: str) -> str:
    encrypted_token = (encrypted_token or "").strip()

    if not encrypted_token:
        raise ValueError("Encrypted token cannot be empty")

    try:
        return _fernet().decrypt(
            encrypted_token.encode("utf-8")
        ).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt Shopify access token") from exc
