"""Secret management with encryption at rest.

Stores secrets using a versioned encrypt-then-MAC construction derived from a
required environment master key.  The implementation is dependency-free and
detects ciphertext tampering before decryption. Existing unversioned values
from the draft XOR format remain readable so they can be rotated in place.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_schema(db: Any) -> None:
    """Ensure secrets table exists."""
    conn = db._get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS secrets (
            secret_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            encrypted_value TEXT NOT NULL,
            description TEXT DEFAULT '',
            tenant_id TEXT DEFAULT 'default',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name, tenant_id)
        );
    """)


class SecretManager:
    """Secret management with encryption at rest.

    Stores secrets encrypted in the database using a master key
    derived from an environment variable.
    """

    def __init__(
        self,
        db: Any,
        master_key_env: str = "NOERELAY_MASTER_KEY",
        *,
        master_key: str | None = None,
    ) -> None:
        self._db = db
        self._set_master_key(self._load_master_key(master_key_env, master_key))
        _ensure_schema(db)

    @staticmethod
    def _load_master_key(env_var: str, explicit: str | None) -> str:
        """Load a non-empty master key or fail closed."""
        raw = explicit if explicit is not None else os.environ.get(env_var)
        if not raw or not raw.strip():
            raise SecretConfigurationError(
                f"{env_var} must be set before the secret store can be enabled"
            )
        return raw

    def _set_master_key(self, raw: str) -> None:
        root = hashlib.sha256(raw.encode("utf-8")).digest()
        self._legacy_key = root
        self._encryption_key = hmac.new(
            root, b"noerelay-secret-encryption-v1", hashlib.sha256
        ).digest()
        self._authentication_key = hmac.new(
            root, b"noerelay-secret-authentication-v1", hashlib.sha256
        ).digest()

    def _keystream(self, nonce: bytes, length: int) -> bytes:
        blocks: list[bytes] = []
        counter = 0
        while sum(map(len, blocks)) < length:
            blocks.append(
                hmac.new(
                    self._encryption_key,
                    b"stream-v1" + nonce + counter.to_bytes(8, "big"),
                    hashlib.sha256,
                ).digest()
            )
            counter += 1
        return b"".join(blocks)[:length]

    def _encrypt(self, plaintext: str) -> str:
        """Encrypt and authenticate plaintext with a random nonce."""
        nonce = secrets.token_bytes(16)
        plaintext_bytes = plaintext.encode("utf-8")
        stream = self._keystream(nonce, len(plaintext_bytes))
        ciphertext = bytes(
            plaintext_byte ^ stream_byte
            for plaintext_byte, stream_byte in zip(plaintext_bytes, stream)
        )
        tag = hmac.new(
            self._authentication_key,
            b"v1" + nonce + ciphertext,
            hashlib.sha256,
        ).digest()
        return "v1:" + base64.b64encode(nonce + ciphertext + tag).decode("ascii")

    def _decrypt(self, ciphertext: str) -> str:
        """Verify and decrypt ciphertext, including the legacy draft format."""
        if not ciphertext.startswith("v1:"):
            encrypted = base64.b64decode(ciphertext, validate=True)
            decrypted = bytes(
                encrypted_byte ^ self._legacy_key[i % len(self._legacy_key)]
                for i, encrypted_byte in enumerate(encrypted)
            )
            return decrypted.decode("utf-8")

        try:
            blob = base64.b64decode(ciphertext[3:], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise SecretIntegrityError("secret ciphertext is malformed") from exc
        if len(blob) < 48:
            raise SecretIntegrityError("secret ciphertext is truncated")
        nonce, encrypted_and_tag = blob[:16], blob[16:]
        encrypted, tag = encrypted_and_tag[:-32], encrypted_and_tag[-32:]
        expected_tag = hmac.new(
            self._authentication_key,
            b"v1" + nonce + encrypted,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(tag, expected_tag):
            raise SecretIntegrityError("secret ciphertext authentication failed")
        stream = self._keystream(nonce, len(encrypted))
        plaintext = bytes(
            encrypted_byte ^ stream_byte
            for encrypted_byte, stream_byte in zip(encrypted, stream)
        )
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretIntegrityError("secret plaintext is not valid UTF-8") from exc

    def store_secret(
        self,
        name: str,
        value: str,
        description: str = "",
        tenant_id: str = "default",
    ) -> str:
        """Store an encrypted secret."""
        encrypted = self._encrypt(value)
        secret_id = f"secret-{uuid.uuid4().hex}"
        now = _now()

        conn = self._db._get_conn()
        # Check if secret with this name already exists for this tenant
        existing = conn.execute(
            "SELECT secret_id FROM secrets WHERE name = ? AND tenant_id = ?",
            (name, tenant_id),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE secrets SET encrypted_value = ?, description = ?, updated_at = ?
                   WHERE name = ? AND tenant_id = ?""",
                (encrypted, description, now, name, tenant_id),
            )
            return existing["secret_id"]
        else:
            conn.execute(
                """INSERT INTO secrets (secret_id, name, encrypted_value, description, tenant_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (secret_id, name, encrypted, description, tenant_id, now, now),
            )
            return secret_id

    def get_secret(self, name: str, tenant_id: str = "default") -> str | None:
        """Retrieve and decrypt a secret."""
        conn = self._db._get_conn()
        row = conn.execute(
            "SELECT encrypted_value FROM secrets WHERE name = ? AND tenant_id = ?",
            (name, tenant_id),
        ).fetchone()
        if row is None:
            return None
        return self._decrypt(row["encrypted_value"])

    def list_secrets(self, tenant_id: str = "default") -> list[dict[str, Any]]:
        """List secret names (without values)."""
        conn = self._db._get_conn()
        rows = conn.execute(
            "SELECT secret_id, name, description, tenant_id, created_at, updated_at "
            "FROM secrets WHERE tenant_id = ? ORDER BY name",
            (tenant_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_secret(self, name: str, tenant_id: str = "default") -> bool:
        """Delete a secret."""
        conn = self._db._get_conn()
        cursor = conn.execute(
            "DELETE FROM secrets WHERE name = ? AND tenant_id = ?",
            (name, tenant_id),
        )
        return cursor.rowcount > 0

    def rotate_master_key(self, new_master_key: str) -> None:
        """Rotate the master key (re-encrypts all secrets)."""
        if not new_master_key.strip():
            raise SecretConfigurationError("new master key must not be empty")

        conn = self._db._get_conn()
        rows = conn.execute("SELECT secret_id, name, encrypted_value, tenant_id FROM secrets").fetchall()
        plaintexts = [(row["secret_id"], self._decrypt(row["encrypted_value"])) for row in rows]
        old_keys = (
            self._legacy_key,
            self._encryption_key,
            self._authentication_key,
        )
        self._set_master_key(new_master_key)
        replacements = [
            (self._encrypt(plaintext), _now(), secret_id)
            for secret_id, plaintext in plaintexts
        ]
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "UPDATE secrets SET encrypted_value = ?, updated_at = ? WHERE secret_id = ?",
                replacements,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            (
                self._legacy_key,
                self._encryption_key,
                self._authentication_key,
            ) = old_keys
            raise


class SecretConfigurationError(RuntimeError):
    """Raised when the secret store cannot be initialized safely."""


class SecretIntegrityError(RuntimeError):
    """Raised when stored ciphertext is malformed or fails authentication."""
