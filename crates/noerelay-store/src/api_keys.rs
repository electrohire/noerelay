//! PostgreSQL repository for API key lifecycle management.
//!
//! Provides [`ApiKeyRepository`] with operations for:
//! - One-time key issuance with Argon2id hashing
//! - Constant-time verification against stored hashes
//! - Immediate revocation
//! - Atomic rotation (revoke old + issue new in a transaction)
//! - Per-key rate limiting (sliding window)
//! - Concurrency tracking
//! - Immutable audit logging
//!
//! The plaintext secret is NEVER stored or logged. It is returned exactly once
//! at issuance time and must be captured by the caller.

use argon2::{
    Argon2,
    password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
};
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::{self, Timelike};
use noerelay_core::iam::*;
use rand::Rng;
use sqlx::{PgPool, Postgres, Row, Transaction};
use thiserror::Error;
use uuid::Uuid;

use crate::iam::set_tenant_context;

// ============================================================================
// Error Type
// ============================================================================

#[derive(Debug, Error)]
pub enum ApiKeyStoreError {
    #[error("database operation failed: {0}")]
    Database(#[from] sqlx::Error),
    #[error("API key not found: {0}")]
    NotFound(String),
    #[error("API key already exists: {0}")]
    AlreadyExists(String),
    #[error("API key has expired")]
    Expired,
    #[error("API key has been revoked")]
    Revoked,
    #[error("rate limit exceeded")]
    RateLimitExceeded,
    #[error("concurrency limit exceeded")]
    ConcurrencyExceeded,
    #[error("hash verification failed: {0}")]
    HashError(String),
}

// ============================================================================
// Repository
// ============================================================================

/// Repository for API key lifecycle operations.
///
/// All methods that interact with tenant-bearing tables begin a transaction,
/// set the RLS context via [`set_tenant_context`], execute queries, and
/// commit. This ensures pooled connections never leak tenant context.
#[derive(Clone)]
pub struct ApiKeyRepository {
    pool: PgPool,
}

impl ApiKeyRepository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    // ========================================================================
    // Key Issuance
    // ========================================================================

    /// Issue a new API key. The plaintext secret is returned exactly once.
    ///
    /// # Security
    /// - Generates a versioned prefix (e.g., "nr_live_v1_" + 8 random chars)
    /// - Generates 32 bytes of high-entropy secret, base64url-encoded
    /// - Hashes the secret with Argon2id before storage
    /// - Records an immutable audit event "created"
    /// - The plaintext secret is NEVER stored or logged
    #[allow(clippy::too_many_arguments)]
    pub async fn issue_key(
        &self,
        principal_id: PrincipalId,
        organization_id: OrganizationId,
        project_id: Option<ProjectId>,
        environment_id: Option<EnvironmentId>,
        role_id: Option<RoleId>,
        name: &str,
        expires_at: Option<chrono::DateTime<chrono::Utc>>,
        rate_limit_per_minute: Option<i32>,
        concurrency_limit: Option<i32>,
    ) -> Result<ApiKeyIssuance, ApiKeyStoreError> {
        let org_id_str = organization_id.0.to_string();
        let key_id = Uuid::new_v4();
        let prefix = generate_prefix();
        let secret_bytes: [u8; 32] = rand::thread_rng().r#gen();
        let secret_str = URL_SAFE_NO_PAD.encode(secret_bytes);
        let secret = ApiKeySecret(secret_str.clone());

        // Hash the secret with Argon2id
        let salt = SaltString::generate(&mut rand::thread_rng());
        let argon2 = Argon2::default();
        let hash = argon2
            .hash_password(secret_str.as_bytes(), &salt)
            .map_err(|e| ApiKeyStoreError::HashError(e.to_string()))?
            .to_string();

        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, Some(principal_id)).await?;

        let row = sqlx::query(
            "INSERT INTO api_keys \
             (id, principal_id, organization_id, project_id, environment_id, role_id, \
              name, prefix, key_hash, status, expires_at, \
              rate_limit_per_minute, concurrency_limit) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'active', $10, $11, $12) \
             RETURNING id, principal_id, organization_id, project_id, environment_id, \
                       role_id, name, prefix, key_hash, status, expires_at, \
                       last_used_at, last_used_ip, rate_limit_per_minute, \
                       concurrency_limit, created_at, revoked_at, revoked_by, revoked_reason",
        )
        .bind(key_id)
        .bind(principal_id.0)
        .bind(&org_id_str)
        .bind(project_id.map(|p| p.0.to_string()))
        .bind(environment_id.map(|e| e.0))
        .bind(role_id.map(|r| r.0))
        .bind(name)
        .bind(&prefix.0)
        .bind(&hash)
        .bind(expires_at)
        .bind(rate_limit_per_minute)
        .bind(concurrency_limit)
        .fetch_one(&mut *tx)
        .await?;

        let api_key = row_to_api_key(&row)?;

        // Record audit event
        record_audit_event(
            &mut tx,
            key_id,
            "created",
            Some(principal_id),
            None,
            None,
            None,
        )
        .await?;

        tx.commit().await?;

        Ok(ApiKeyIssuance {
            api_key,
            secret,
            warning: "Store this secret securely. It will not be shown again.".into(),
        })
    }

    // ========================================================================
    // Key Verification
    // ========================================================================

    /// Verify an API key by prefix and secret.
    ///
    /// Performs constant-time comparison of the provided secret against the
    /// stored Argon2id hash. Updates `last_used_at` and `last_used_ip` on
    /// successful verification. Records an audit event for every verification
    /// attempt (success or failure).
    pub async fn verify_key(
        &self,
        prefix: &str,
        secret: &str,
        ip: Option<&str>,
    ) -> Result<ApiKeyVerification, ApiKeyStoreError> {
        let mut tx = self.pool.begin().await?;

        // Look up by prefix (no tenant context needed for lookup)
        let row = sqlx::query(
            "SELECT id, principal_id, organization_id, project_id, environment_id, \
                    role_id, name, prefix, key_hash, status, expires_at, \
                    last_used_at, last_used_ip, rate_limit_per_minute, \
                    concurrency_limit, created_at, revoked_at, revoked_by, revoked_reason \
             FROM api_keys WHERE prefix = $1",
        )
        .bind(prefix)
        .fetch_optional(&mut *tx)
        .await?;

        let row = match row {
            Some(r) => r,
            None => {
                tx.commit().await?;
                return Ok(ApiKeyVerification {
                    valid: false,
                    api_key: None,
                    failure_reason: Some("not_found".into()),
                });
            }
        };

        let api_key = row_to_api_key(&row)?;
        let key_id = api_key.id.0;

        // Check status
        if api_key.status == EntityStatus::Revoked {
            record_audit_event(&mut tx, key_id, "verified", None, ip, None, None).await?;
            tx.commit().await?;
            return Ok(ApiKeyVerification {
                valid: false,
                api_key: Some(api_key),
                failure_reason: Some("revoked".into()),
            });
        }

        // Check expiry
        if let Some(expires_at) = api_key.expires_at {
            if chrono::Utc::now() > expires_at {
                record_audit_event(&mut tx, key_id, "expired", None, ip, None, None).await?;
                tx.commit().await?;
                return Ok(ApiKeyVerification {
                    valid: false,
                    api_key: Some(api_key),
                    failure_reason: Some("expired".into()),
                });
            }
        }

        // Verify hash (constant-time via Argon2id)
        let parsed_hash = PasswordHash::new(&api_key.key_hash.0)
            .map_err(|e| ApiKeyStoreError::HashError(e.to_string()))?;
        let argon2 = Argon2::default();
        let hash_valid = argon2
            .verify_password(secret.as_bytes(), &parsed_hash)
            .is_ok();

        if !hash_valid {
            record_audit_event(
                &mut tx,
                key_id,
                "verified",
                None,
                ip,
                Some(serde_json::json!({"result": "hash_mismatch"})),
                None,
            )
            .await?;
            tx.commit().await?;
            return Ok(ApiKeyVerification {
                valid: false,
                api_key: Some(api_key),
                failure_reason: Some("hash_mismatch".into()),
            });
        }

        // Update last_used metadata
        let org_id_str = api_key.organization_id.0.to_string();
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        sqlx::query("UPDATE api_keys SET last_used_at = now(), last_used_ip = $1 WHERE id = $2")
            .bind(ip)
            .bind(key_id)
            .execute(&mut *tx)
            .await?;

        // Record successful verification
        record_audit_event(
            &mut tx,
            key_id,
            "verified",
            None,
            ip,
            Some(serde_json::json!({"result": "success"})),
            None,
        )
        .await?;

        tx.commit().await?;

        Ok(ApiKeyVerification {
            valid: true,
            api_key: Some(api_key),
            failure_reason: None,
        })
    }

    // ========================================================================
    // Key Revocation
    // ========================================================================

    /// Immediately revoke an API key. The key cannot be used after revocation.
    pub async fn revoke_key(
        &self,
        key_id: ApiKeyId,
        revoked_by: PrincipalId,
        reason: &str,
    ) -> Result<(), ApiKeyStoreError> {
        let mut tx = self.pool.begin().await?;

        // Get the key to find its organization
        let row = sqlx::query("SELECT organization_id FROM api_keys WHERE id = $1")
            .bind(key_id.0)
            .fetch_optional(&mut *tx)
            .await?;

        let org_id_str: String = match row {
            Some(r) => r.get("organization_id"),
            None => {
                tx.commit().await?;
                return Err(ApiKeyStoreError::NotFound(key_id.0.to_string()));
            }
        };

        set_tenant_context(&mut tx, &org_id_str, Some(revoked_by)).await?;

        let result = sqlx::query(
            "UPDATE api_keys SET status = 'revoked', revoked_at = now(), \
             revoked_by = $1, revoked_reason = $2 WHERE id = $3 AND status != 'revoked'",
        )
        .bind(revoked_by.0)
        .bind(reason)
        .bind(key_id.0)
        .execute(&mut *tx)
        .await?;

        if result.rows_affected() == 0 {
            tx.commit().await?;
            return Err(ApiKeyStoreError::NotFound(key_id.0.to_string()));
        }

        record_audit_event(
            &mut tx,
            key_id.0,
            "revoked",
            Some(revoked_by),
            None,
            Some(serde_json::json!({"reason": reason})),
            None,
        )
        .await?;

        tx.commit().await?;
        Ok(())
    }

    // ========================================================================
    // Key Rotation (atomic: revoke old + issue new)
    // ========================================================================

    /// Atomically rotate an API key: revoke the old key and issue a new one
    /// with the same scopes. Returns the new key issuance with one-time secret.
    pub async fn rotate_key(
        &self,
        key_id: ApiKeyId,
        rotated_by: PrincipalId,
    ) -> Result<ApiKeyIssuance, ApiKeyStoreError> {
        let mut tx = self.pool.begin().await?;

        // Fetch the existing key
        let row = sqlx::query(
            "SELECT id, principal_id, organization_id, project_id, environment_id, \
                    role_id, name, prefix, key_hash, status, expires_at, \
                    last_used_at, last_used_ip, rate_limit_per_minute, \
                    concurrency_limit, created_at, revoked_at, revoked_by, revoked_reason \
             FROM api_keys WHERE id = $1 FOR UPDATE",
        )
        .bind(key_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        let row = match row {
            Some(r) => r,
            None => {
                tx.commit().await?;
                return Err(ApiKeyStoreError::NotFound(key_id.0.to_string()));
            }
        };

        let old_key = row_to_api_key(&row)?;
        let org_id_str = old_key.organization_id.0.to_string();

        set_tenant_context(&mut tx, &org_id_str, Some(rotated_by)).await?;

        // Revoke the old key
        sqlx::query(
            "UPDATE api_keys SET status = 'revoked', revoked_at = now(), \
             revoked_by = $1, revoked_reason = 'rotated' WHERE id = $2",
        )
        .bind(rotated_by.0)
        .bind(key_id.0)
        .execute(&mut *tx)
        .await?;

        // Issue new key with same scopes
        let new_key_id = Uuid::new_v4();
        let prefix = generate_prefix();
        let secret_bytes: [u8; 32] = rand::thread_rng().r#gen();
        let secret_str = URL_SAFE_NO_PAD.encode(secret_bytes);
        let secret = ApiKeySecret(secret_str.clone());

        let salt = SaltString::generate(&mut rand::thread_rng());
        let argon2 = Argon2::default();
        let hash = argon2
            .hash_password(secret_str.as_bytes(), &salt)
            .map_err(|e| ApiKeyStoreError::HashError(e.to_string()))?
            .to_string();

        let new_row = sqlx::query(
            "INSERT INTO api_keys \
             (id, principal_id, organization_id, project_id, environment_id, role_id, \
              name, prefix, key_hash, status, expires_at, \
              rate_limit_per_minute, concurrency_limit) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'active', $10, $11, $12) \
             RETURNING id, principal_id, organization_id, project_id, environment_id, \
                       role_id, name, prefix, key_hash, status, expires_at, \
                       last_used_at, last_used_ip, rate_limit_per_minute, \
                       concurrency_limit, created_at, revoked_at, revoked_by, revoked_reason",
        )
        .bind(new_key_id)
        .bind(old_key.principal_id.0)
        .bind(&org_id_str)
        .bind(old_key.project_id.map(|p| p.0.to_string()))
        .bind(old_key.environment_id.map(|e| e.0))
        .bind(old_key.role_id.map(|r| r.0))
        .bind(&old_key.name)
        .bind(&prefix.0)
        .bind(&hash)
        .bind(old_key.expires_at)
        .bind(old_key.rate_limit_per_minute)
        .bind(old_key.concurrency_limit)
        .fetch_one(&mut *tx)
        .await?;

        let new_api_key = row_to_api_key(&new_row)?;

        // Record audit events
        record_audit_event(
            &mut tx,
            key_id.0,
            "revoked",
            Some(rotated_by),
            None,
            Some(serde_json::json!({"reason": "rotated", "replaced_by": new_key_id})),
            None,
        )
        .await?;

        record_audit_event(
            &mut tx,
            new_key_id,
            "rotated",
            Some(rotated_by),
            None,
            Some(serde_json::json!({"replaced": key_id.0})),
            None,
        )
        .await?;

        tx.commit().await?;

        Ok(ApiKeyIssuance {
            api_key: new_api_key,
            secret,
            warning: "Store this secret securely. It will not be shown again.".into(),
        })
    }

    // ========================================================================
    // Rate Limiting
    // ========================================================================

    /// Check and enforce per-key rate limiting using a sliding window.
    ///
    /// If the key has no `rate_limit_per_minute` configured, the request is
    /// always allowed. Otherwise, counts requests in the current minute window
    /// and returns a [`RateLimitDecision`].
    pub async fn check_rate_limit(
        &self,
        key_id: ApiKeyId,
    ) -> Result<RateLimitDecision, ApiKeyStoreError> {
        let mut tx = self.pool.begin().await?;

        // Get the key's rate limit
        let row = sqlx::query(
            "SELECT rate_limit_per_minute, organization_id FROM api_keys WHERE id = $1",
        )
        .bind(key_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        let (limit, org_id_str): (Option<i32>, String) = match row {
            Some(r) => (r.get("rate_limit_per_minute"), r.get("organization_id")),
            None => {
                tx.commit().await?;
                return Err(ApiKeyStoreError::NotFound(key_id.0.to_string()));
            }
        };

        let limit = match limit {
            Some(l) if l > 0 => l,
            _ => {
                tx.commit().await?;
                return Ok(RateLimitDecision {
                    allowed: true,
                    remaining: -1, // unlimited
                    reset_at: chrono::Utc::now(),
                    limit: -1,
                });
            }
        };

        set_tenant_context(&mut tx, &org_id_str, None).await?;

        // Current minute window (truncated to minute)
        let now = chrono::Utc::now();
        let window_start = now
            .date_naive()
            .and_hms_opt(now.hour(), now.minute(), 0)
            .and_then(|dt| dt.and_local_timezone(chrono::Utc).earliest())
            .unwrap_or(now);

        // Upsert the rate limit counter
        sqlx::query(
            "INSERT INTO api_key_rate_limits (api_key_id, window_start, request_count) \
             VALUES ($1, $2, 1) \
             ON CONFLICT (api_key_id, window_start) \
             DO UPDATE SET request_count = api_key_rate_limits.request_count + 1 \
             RETURNING request_count",
        )
        .bind(key_id.0)
        .bind(window_start)
        .fetch_one(&mut *tx)
        .await?;

        // Read current count
        let count_row = sqlx::query(
            "SELECT request_count FROM api_key_rate_limits \
             WHERE api_key_id = $1 AND window_start = $2",
        )
        .bind(key_id.0)
        .bind(window_start)
        .fetch_one(&mut *tx)
        .await?;

        let count: i32 = count_row.get("request_count");
        let remaining = (limit - count).max(0);
        let reset_at = window_start + chrono::Duration::minutes(1);

        if count > limit {
            // Record rate limit audit event
            record_audit_event(
                &mut tx,
                key_id.0,
                "rate_limited",
                None,
                None,
                Some(serde_json::json!({"count": count, "limit": limit})),
                None,
            )
            .await?;

            tx.commit().await?;
            return Ok(RateLimitDecision {
                allowed: false,
                remaining: 0,
                reset_at,
                limit,
            });
        }

        tx.commit().await?;
        Ok(RateLimitDecision {
            allowed: true,
            remaining,
            reset_at,
            limit,
        })
    }

    // ========================================================================
    // Concurrency Tracking
    // ========================================================================

    /// Attempt to acquire a concurrency slot for the given key.
    ///
    /// Returns `true` if the slot was acquired, `false` if the concurrency
    /// limit has been reached.
    pub async fn acquire_concurrency(&self, key_id: ApiKeyId) -> Result<bool, ApiKeyStoreError> {
        let mut tx = self.pool.begin().await?;

        let row = sqlx::query(
            "SELECT concurrency_limit, current_concurrency, organization_id \
             FROM api_keys WHERE id = $1 FOR UPDATE",
        )
        .bind(key_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        let (limit, current, org_id_str): (Option<i32>, i32, String) = match row {
            Some(r) => (
                r.get("concurrency_limit"),
                r.get("current_concurrency"),
                r.get("organization_id"),
            ),
            None => {
                tx.commit().await?;
                return Err(ApiKeyStoreError::NotFound(key_id.0.to_string()));
            }
        };

        let limit = match limit {
            Some(l) if l > 0 => l,
            _ => {
                // No concurrency limit configured
                tx.commit().await?;
                return Ok(true);
            }
        };

        if current >= limit {
            tx.commit().await?;
            return Ok(false);
        }

        set_tenant_context(&mut tx, &org_id_str, None).await?;

        sqlx::query(
            "UPDATE api_keys SET current_concurrency = current_concurrency + 1 WHERE id = $1",
        )
        .bind(key_id.0)
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;
        Ok(true)
    }

    /// Release a concurrency slot for the given key.
    pub async fn release_concurrency(&self, key_id: ApiKeyId) -> Result<(), ApiKeyStoreError> {
        let mut tx = self.pool.begin().await?;

        let row = sqlx::query("SELECT organization_id FROM api_keys WHERE id = $1")
            .bind(key_id.0)
            .fetch_optional(&mut *tx)
            .await?;

        let org_id_str: String = match row {
            Some(r) => r.get("organization_id"),
            None => {
                tx.commit().await?;
                return Err(ApiKeyStoreError::NotFound(key_id.0.to_string()));
            }
        };

        set_tenant_context(&mut tx, &org_id_str, None).await?;

        sqlx::query(
            "UPDATE api_keys SET current_concurrency = GREATEST(current_concurrency - 1, 0) \
             WHERE id = $1",
        )
        .bind(key_id.0)
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;
        Ok(())
    }

    // ========================================================================
    // Key Listing / Retrieval
    // ========================================================================

    /// List all API keys for a principal (without key hashes in response).
    pub async fn list_keys(
        &self,
        principal_id: PrincipalId,
    ) -> Result<Vec<ApiKey>, ApiKeyStoreError> {
        let mut tx = self.pool.begin().await?;

        let rows = sqlx::query(
            "SELECT id, principal_id, organization_id, project_id, environment_id, \
                    role_id, name, prefix, key_hash, status, expires_at, \
                    last_used_at, last_used_ip, rate_limit_per_minute, \
                    concurrency_limit, created_at, revoked_at, revoked_by, revoked_reason \
             FROM api_keys WHERE principal_id = $1 \
             ORDER BY created_at DESC",
        )
        .bind(principal_id.0)
        .fetch_all(&mut *tx)
        .await?;

        tx.commit().await?;
        rows.iter().map(row_to_api_key).collect()
    }

    /// Get a single API key by ID (without key hash in response).
    pub async fn get_key(&self, key_id: ApiKeyId) -> Result<ApiKey, ApiKeyStoreError> {
        let mut tx = self.pool.begin().await?;

        let row = sqlx::query(
            "SELECT id, principal_id, organization_id, project_id, environment_id, \
                    role_id, name, prefix, key_hash, status, expires_at, \
                    last_used_at, last_used_ip, rate_limit_per_minute, \
                    concurrency_limit, created_at, revoked_at, revoked_by, revoked_reason \
             FROM api_keys WHERE id = $1",
        )
        .bind(key_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;

        match row {
            Some(r) => row_to_api_key(&r),
            None => Err(ApiKeyStoreError::NotFound(key_id.0.to_string())),
        }
    }
}

// ============================================================================
// Helpers
// ============================================================================

/// Generate a versioned API key prefix.
///
/// Format: "nr_live_v1_" + 8 random alphanumeric characters
fn generate_prefix() -> ApiKeyPrefix {
    let random_part: String = rand::thread_rng()
        .sample_iter(&rand::distributions::Alphanumeric)
        .take(8)
        .map(char::from)
        .collect();
    ApiKeyPrefix(format!("nr_live_v1_{}", random_part.to_lowercase()))
}

/// Convert a database row to an [`ApiKey`].
fn row_to_api_key(row: &sqlx::postgres::PgRow) -> Result<ApiKey, ApiKeyStoreError> {
    let status_str: String = row.try_get("status")?;
    let status = match status_str.as_str() {
        "active" => EntityStatus::Active,
        "suspended" => EntityStatus::Suspended,
        "revoked" => EntityStatus::Revoked,
        "archived" => EntityStatus::Archived,
        _ => EntityStatus::Active,
    };

    Ok(ApiKey {
        id: ApiKeyId(row.try_get("id")?),
        principal_id: PrincipalId(row.try_get("principal_id")?),
        organization_id: {
            let org_str: String = row.try_get("organization_id")?;
            OrganizationId(Uuid::parse_str(&org_str).unwrap_or_else(|_| Uuid::nil()))
        },
        project_id: {
            let proj_str: Option<String> = row.try_get("project_id")?;
            proj_str.map(|s| ProjectId(Uuid::parse_str(&s).unwrap_or_else(|_| Uuid::nil())))
        },
        environment_id: row
            .try_get::<Option<Uuid>, _>("environment_id")?
            .map(EnvironmentId),
        role_id: row.try_get::<Option<Uuid>, _>("role_id")?.map(RoleId),
        name: row.try_get("name")?,
        prefix: ApiKeyPrefix(row.try_get("prefix")?),
        key_hash: ApiKeyHash(row.try_get("key_hash")?),
        status,
        expires_at: row.try_get("expires_at")?,
        last_used_at: row.try_get("last_used_at")?,
        last_used_ip: row.try_get("last_used_ip")?,
        rate_limit_per_minute: row.try_get("rate_limit_per_minute")?,
        concurrency_limit: row.try_get("concurrency_limit")?,
        created_at: row.try_get("created_at")?,
        revoked_at: row.try_get("revoked_at")?,
        revoked_by: row
            .try_get::<Option<Uuid>, _>("revoked_by")?
            .map(PrincipalId),
        revoked_reason: row.try_get("revoked_reason")?,
    })
}

/// Record an immutable audit event for an API key operation.
async fn record_audit_event(
    tx: &mut Transaction<'_, Postgres>,
    api_key_id: Uuid,
    event_type: &str,
    principal_id: Option<PrincipalId>,
    ip_address: Option<&str>,
    detail: Option<serde_json::Value>,
    user_agent: Option<&str>,
) -> Result<(), ApiKeyStoreError> {
    sqlx::query(
        "INSERT INTO api_key_audit_log \
         (api_key_id, event_type, principal_id, ip_address, user_agent, detail) \
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(api_key_id)
    .bind(event_type)
    .bind(principal_id.map(|p| p.0))
    .bind(ip_address)
    .bind(user_agent)
    .bind(detail)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generate_prefix_has_correct_format() {
        let prefix = generate_prefix();
        assert!(prefix.0.starts_with("nr_live_v1_"));
        assert_eq!(prefix.0.len(), "nr_live_v1_".len() + 8);
    }

    #[test]
    fn api_key_secret_display_is_redacted() {
        let secret = ApiKeySecret("super-secret-value".into());
        assert_eq!(format!("{}", secret), "[REDACTED]");
        // Display is the redacted form; Debug on transparent newtype shows inner value
        assert!(!format!("{}", secret).contains("super-secret-value"));
    }
}
