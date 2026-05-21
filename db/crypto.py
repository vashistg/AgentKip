import os
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ.get("CREDENTIALS_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("CREDENTIALS_ENCRYPTION_KEY not set in environment")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
