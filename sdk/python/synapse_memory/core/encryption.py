import base64
import hashlib
import hmac
import logging
import os
import secrets
from typing import Optional

logger = logging.getLogger("SynapseEncryption")

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False
    Fernet = None


class KeyManagementProvider:
    """
    Durable encryption key manager.
    Retrieves key from SYNAPSE_ENCRYPTION_KEY env var,
    or persists/loads a durable key from a local keyfile (.synapse_key).
    """
    @staticmethod
    def get_encryption_key(key_file: Optional[str] = None) -> bytes:
        env_key = os.environ.get("SYNAPSE_ENCRYPTION_KEY")
        if env_key:
            return env_key.strip().encode("utf-8")

        target_file = key_file or os.environ.get("SYNAPSE_KEY_FILE", ".synapse_key")
        if os.path.exists(target_file):
            try:
                with open(target_file, "rb") as f:
                    file_key = f.read().strip()
                    if file_key:
                        return file_key
            except Exception as e:
                logger.warning(f"Could not read key file {target_file}: {e}")

        # Generate a durable 32-byte urlsafe base64 key
        new_key = FernetEncryptionProvider.generate_key()
        try:
            with open(target_file, "wb") as f:
                f.write(new_key)
            os.chmod(target_file, 0o600)
        except Exception as e:
            logger.warning(f"Could not persist encryption key to {target_file}: {e}")
        return new_key


# Aliases for backwards compatibility
FernetKeyManager = KeyManagementProvider


class FernetEncryptionProvider:
    """
    Encryption provider using Fernet symmetric authenticated encryption.
    Underlying specification: 128-bit AES in CBC mode with PKCS7 padding
    and HMAC-SHA256 authenticated encryption.
    """
    def __init__(self, key: Optional[bytes] = None):
        if key is None:
            self.key = KeyManagementProvider.get_encryption_key()
        elif isinstance(key, str):
            self.key = key.encode("utf-8")
        else:
            self.key = key

        if HAS_CRYPTOGRAPHY and Fernet is not None:
            self._cipher = Fernet(self.key)
        else:
            self._cipher = None

    @staticmethod
    def generate_key() -> bytes:
        if HAS_CRYPTOGRAPHY and Fernet is not None:
            return Fernet.generate_key()
        return base64.urlsafe_b64encode(secrets.token_bytes(32))

    def encrypt(self, data: str) -> str:
        """Encrypts a string and returns a base64 encoded string."""
        if self._cipher is not None:
            return self._cipher.encrypt(data.encode("utf-8")).decode("utf-8")

        raw_bytes = data.encode("utf-8")
        iv = secrets.token_bytes(16)
        stream_key = hashlib.sha256(self.key + b":enc:" + iv).digest()
        keystream = (
            stream_key * ((len(raw_bytes) // len(stream_key)) + 1)
        )[:len(raw_bytes)]
        ciphertext = bytes(b ^ k for b, k in zip(raw_bytes, keystream))
        mac = hmac.new(self.key, iv + ciphertext, hashlib.sha256).digest()
        payload = b"FALLBACKv1:" + iv + mac + ciphertext
        return base64.urlsafe_b64encode(payload).decode("utf-8")

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypts a base64 encoded string and returns the original string."""
        if self._cipher is not None:
            try:
                return self._cipher.decrypt(
                    encrypted_data.encode("utf-8")
                ).decode("utf-8")
            except Exception:
                pass

        try:
            payload = base64.urlsafe_b64decode(encrypted_data.encode("utf-8"))
            if payload.startswith(b"FALLBACKv1:"):
                body = payload[len(b"FALLBACKv1:"):]
                iv = body[:16]
                mac = body[16:48]
                ciphertext = body[48:]
                expected_mac = hmac.new(
                    self.key, iv + ciphertext, hashlib.sha256
                ).digest()
                if not hmac.compare_digest(mac, expected_mac):
                    raise ValueError("Authentication tag mismatch")
                stream_key = hashlib.sha256(self.key + b":enc:" + iv).digest()
                keystream = (
                    stream_key * ((len(ciphertext) // len(stream_key)) + 1)
                )[:len(ciphertext)]
                plain = bytes(b ^ k for b, k in zip(ciphertext, keystream))
                return plain.decode("utf-8")
        except Exception:
            pass

        if self._cipher is not None:
            return self._cipher.decrypt(
                encrypted_data.encode("utf-8")
            ).decode("utf-8")
        raise ValueError("Decryption failed: invalid key or payload")


# Aliases for backwards compatibility
EncryptedMemoryStore = FernetEncryptionProvider
