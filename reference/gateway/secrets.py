"""Secret management with encryption at rest.

Stores secrets (API keys, tokens) encrypted in the database
using a master key derived from an environment variable.

For production: integrate with HashiCorp Vault or AWS Secrets Manager.
For skeleton: use XOR-based encryption with stdlib (base64 + hashlib).
"""

from __future__ import annotations

import base64
import hashlib
import os
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

    def __init__(self, db: Any, master_key_env: str = "NOERELAY_MASTER_KEY") -> None:
        self._db = db
        self._master_key = self._derive_master_key(master_key_env)
        _ensure_schema(db)

    def _derive_master_key(self, env_var: str) -> bytes:
        """Derive a master key from an environment variable."""
        raw = os.environ.get(env_var, "noerelay-default-master-key-change-me")
        return hashlib.sha256(raw.encode()).digest()

    def _encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext using XOR cipher with the master key (simplified).

        Note: For production, use proper AES encryption via cryptography library
        or integrate with Vault. This is a skeleton implementation.
        """
        key = self._master_key
        plaintext_bytes = plaintext.encode("utf-8")
        encrypted = bytes(
            plaintext_byte ^ key[i % len(key)]
            for i, plaintext_byte in enumerate(plaintext_bytes)
        )
        return base64.b64encode(encrypted).decode()

    def _decrypt(self, ciphertext: str) -> str:
        """Decrypt ciphertext."""
        key = self._master_key
        encrypted = base64.b64decode(ciphertext)
        decrypted = bytes(
            encrypted_byte ^ key[i % len(key)]
            for i, encrypted_byte in enumerate(encrypted)
        )
        return decrypted.decode("utf-8")

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
        old_key = self._master_key
        new_key = hashlib.sha256(new_master_key.encode()).digest()

        conn = self._db._get_conn()
        rows = conn.execute("SELECT secret_id, name, encrypted_value, tenant_id FROM secrets").fetchall()

        for row in rows:
            # Decrypt with old key
            old_encrypted = base64.b64decode(row["encrypted_value"])
            old_decrypted = bytes(
                old_encrypted_byte ^ old_key[i % len(old_key)]
                for i, old_encrypted_byte in enumerate(old_encrypted)
            )

            # Re-encrypt with new key
            new_encrypted = bytes(
                plaintext_byte ^ new_key[i % len(new_key)]
                for i, plaintext_byte in enumerate(old_decrypted)
            )
            new_ciphertext = base64.b64encode(new_encrypted).decode()

            conn.execute(
                "UPDATE secrets SET encrypted_value = ?, updated_at = ? WHERE secret_id = ?",
                (new_ciphertext, _now(), row["secret_id"]),
            )

        self._master_key = new_key