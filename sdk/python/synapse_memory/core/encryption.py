import logging
import os
from typing import Any, Optional

logger = logging.getLogger("SynapseEncryption")

Fernet: Any
try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False
    Fernet = None


class KeyManagementProvider:
    """
    Durable encryption key manager with team-safe loading order.

    Resolution order (first hit wins):
      1. `SYNAPSE_ENCRYPTION_KEY` — raw 32-byte urlsafe-base64 Fernet key.
      2. `SYNAPSE_ENCRYPTION_KEY_B64_FILE` — path to a file containing ONLY the key.
         (The correct way to inject a secret in Docker/K8s without baking it
         into the image or leaving it in process args.)
      3. KMS/Vault provider hook: if `SYNAPSE_KMS_PROVIDER` is set, the matching
         loader below is invoked with `SYNAPSE_KMS_KEY_REF`. Providers are
         best-effort optional — unknown or misconfigured providers raise, never
         silently fall through, so a prod boot fails loudly instead of
         encrypting with a throwaway key.
      4. Local keyfile (dev fallback): `SYNAPSE_KEY_FILE` (default `.synapse_key`).

    Production deployments MUST use (1), (2), or (3). The local keyfile (4) is a
    convenience that must never back team/production data.
    """
    @staticmethod
    def get_encryption_key(key_file: Optional[str] = None) -> bytes:
        # 1. Direct env key.
        env_key = os.environ.get("SYNAPSE_ENCRYPTION_KEY")
        if env_key:
            return env_key.strip().encode("utf-8")

        # 2. Key-from-file (Docker secrets / K8s secret volume mount).
        b64_file = os.environ.get("SYNAPSE_ENCRYPTION_KEY_B64_FILE")
        if b64_file:
            try:
                with open(b64_file, "rb") as f:
                    file_key = f.read().strip()
                    if file_key:
                        logger.info("Loaded encryption key from SYNAPSE_ENCRYPTION_KEY_B64_FILE.")
                        return file_key
            except Exception as e:
                logger.warning(f"Could not read key file {b64_file}: {e}")

        # 3. KMS/Vault provider hook.
        kms_provider = os.environ.get("SYNAPSE_KMS_PROVIDER")
        if kms_provider:
            kms_ref = os.environ.get("SYNAPSE_KMS_KEY_REF", "")
            loader = _KMS_LOADERS.get(kms_provider.lower())
            if loader is None:
                raise RuntimeError(
                    f"Unknown SYNAPSE_KMS_PROVIDER '{kms_provider}'. "
                    f"Supported: {sorted(_KMS_LOADERS)}."
                )
            logger.info(f"Loading encryption key via KMS provider '{kms_provider}'.")
            return loader(kms_ref)

        # 4. Local dev fallback keyfile.
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


def _kms_loader_aws(ref: str) -> bytes:
    """AWS KMS: decrypts a base64 ciphertext blob via kms:Decrypt (boto3 optional)."""
    try:
        import base64 as _b64
        import boto3  # type: ignore
    except ImportError as e:
        raise RuntimeError("SYNAPSE_KMS_PROVIDER=aws requires boto3 to be installed.") from e
    region = os.environ.get("SYNAPSE_KMS_REGION") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    client = boto3.client("kms", region_name=region) if region else boto3.client("kms")
    resp = client.decrypt(CiphertextBlob=_b64.b64decode(ref))
    return _b64.urlsafe_b64encode(resp["Plaintext"][:32].ljust(32, b"\0"))


def _kms_loader_vault(ref: str) -> bytes:
    """HashiCorp Vault KV: reads the key field over HTTPS (hvac optional)."""
    try:
        import hvac  # type: ignore
    except ImportError as e:
        raise RuntimeError("SYNAPSE_KMS_PROVIDER=vault requires hvac to be installed.") from e
    addr = os.environ.get("VAULT_ADDR", "")
    token = os.environ.get("VAULT_TOKEN", "")
    field = os.environ.get("SYNAPSE_KMS_FIELD", "key")
    if not addr or not token or not ref:
        raise RuntimeError("vault KMS needs VAULT_ADDR, VAULT_TOKEN and SYNAPSE_KMS_KEY_REF (KV path).")
    client = hvac.Client(url=addr, token=token)
    secret = client.secrets.kv.v2.read_secret_version(path=ref)
    value = (secret.get("data", {}).get("data", {}) or {}).get(field, "")
    if not value:
        raise RuntimeError(f"Vault KV path '{ref}' has no '{field}' field.")
    return str(value).strip().encode("utf-8")


def _kms_loader_file(ref: str) -> bytes:
    """Alias of the key-file loader using an explicit path as the ref."""
    with open(ref, "rb") as f:
        return f.read().strip()


_KMS_LOADERS = {
    "aws": _kms_loader_aws,
    "vault": _kms_loader_vault,
    "file": _kms_loader_file,
}


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

        self._cipher: Any = None
        if HAS_CRYPTOGRAPHY and Fernet is not None:
            self._cipher = Fernet(self.key)  # type: ignore[operator]

    @staticmethod
    def generate_key() -> bytes:
        if HAS_CRYPTOGRAPHY and Fernet is not None:
            return Fernet.generate_key()
        raise RuntimeError(
            "Key generation requires the `cryptography` package (Fernet). "
            "Install it with: pip install cryptography"
        )

    def encrypt(self, data: str) -> str:
        """Encrypts a string and returns a base64 encoded string."""
        if self._cipher is None:
            raise RuntimeError(
                "Encryption requires the `cryptography` package (Fernet). "
                "Install it with: pip install cryptography"
            )
        return self._cipher.encrypt(data.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypts a base64 encoded string and returns the original string."""
        if self._cipher is None:
            raise RuntimeError(
                "Decryption requires the `cryptography` package (Fernet). "
                "Install it with: pip install cryptography"
            )
        try:
            return self._cipher.decrypt(
                encrypted_data.encode("utf-8")
            ).decode("utf-8")
        except Exception as e:
            raise ValueError("Decryption failed: invalid key or payload") from e


# Aliases for backwards compatibility
EncryptedMemoryStore = FernetEncryptionProvider
