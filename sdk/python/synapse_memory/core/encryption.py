from cryptography.fernet import Fernet
import base64
from typing import Optional

class FernetEncryptionProvider:
    """
    Encryption provider using Fernet (AES-128/256 symmetric encryption).
    """
    def __init__(self, key: Optional[bytes] = None):
        if key is None:
            self.key = Fernet.generate_key()
        else:
            self.key = key
        self.cipher = Fernet(self.key)

    def encrypt(self, data: str) -> str:
        """Encrypts a string and returns a base64 encoded string."""
        return self.cipher.encrypt(data.encode()).decode()

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypts a base64 encoded string and returns the original string."""
        return self.cipher.decrypt(encrypted_data.encode()).decode()
