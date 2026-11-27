from cryptography.fernet import Fernet

from app.core.config import settings


def monitor_cipher() -> Fernet:
    key = settings.MONITOR_CONFIG_KEY
    if key is None:
        raise ValueError("Configure MONITOR_CONFIG_KEY to store request settings")
    return Fernet(key.get_secret_value().encode())
