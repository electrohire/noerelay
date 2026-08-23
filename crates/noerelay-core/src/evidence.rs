use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Status of an evidence envelope.
///
/// Only `observed_pass` and `accepted` may satisfy an automated release gate.
/// `claimed` is explicitly rejected — a model or agent assertion is not evidence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EnvelopeStatus {
    /// A claim without observed execution (rejected for release gates).
    Claimed,
    /// Observed passing test execution.
    ObservedPass,
    /// Observed failing test execution.
    ObservedFail,
    /// Inferred from other evidence (not directly observed).
    Inferred,
    /// Contradicted by other evidence.
    Contradicted,
    /// Accepted by an independent verifier or human authority.
    Accepted,
    /// Rejected by an independent verifier or human authority.
    Rejected,
}

/// A machine-readable evidence record produced by an automated or manual gate.
///
/// Each envelope binds a test execution to an exact source revision, environment,
/// artifact digests, and runner/verifier identities. The envelope is stored under
/// an immutable release-candidate evidence bundle.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EvidenceEnvelope {
    /// Schema version, always "1.0.0".
    pub evidence_version: String,
    /// Unique identifier for this evidence record (UUID v4).
    pub evidence_id: String,
    /// The work package this evidence belongs to.
    pub work_package_id: String,
    /// Requirement IDs satisfied by this evidence.
    pub requirement_ids: Vec<String>,
    /// Test IDs executed to produce this evidence.
    pub test_ids: Vec<String>,
    /// Observed status of the evidence.
    pub status: EnvelopeStatus,
    /// Full commit SHA of the source revision under test.
    pub source_revision: String,
    /// Map of artifact type to SHA-256 digest (e.g. {"container": "sha256:..."}).
    pub artifact_digests: HashMap<String, String>,
    /// Named deployment profile identifier.
    pub environment_profile: String,
    /// Exact command or manual procedure identifier that produced this evidence.
    pub command: String,
    /// RFC 3339 timestamp when execution started.
    pub started_at: String,
    /// RFC 3339 timestamp when execution finished.
    pub finished_at: String,
    /// Identity of the workload or human that ran the test.
    pub runner_identity: String,
    /// Identity of the independent verifier, required where independence is needed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub independent_verifier_identity: Option<String>,
    /// SHA-256 of the result artifact.
    pub result_artifact_sha256: String,
    /// SHA-256 of the logs artifact.
    pub logs_artifact_sha256: String,
    /// Any exceptions or anomalies encountered during execution.
    #[serde(default)]
    pub exceptions: Vec<String>,
    /// Concise, non-secret rationale or notes.
    #[serde(default)]
    pub notes: String,
}

impl EvidenceEnvelope {
    /// Returns true if this envelope's status is acceptable for a release gate.
    ///
    /// Only `observed_pass` and `accepted` qualify. `claimed` is explicitly rejected.
    pub fn is_release_ready(&self) -> bool {
        matches!(
            self.status,
            EnvelopeStatus::ObservedPass | EnvelopeStatus::Accepted
        )
    }

    /// Returns true if this envelope requires an independent verifier
    /// (i.e., it is used for a release gate and was not independently verified).
    pub fn requires_independent_verifier(&self) -> bool {
        self.independent_verifier_identity.is_none()
            && matches!(
                self.status,
                EnvelopeStatus::ObservedPass | EnvelopeStatus::ObservedFail
            )
    }

    /// Returns true if the source revision looks like a valid commit SHA
    /// (40-character hex string).
    pub fn has_valid_revision(&self) -> bool {
        !self.source_revision.is_empty()
            && self.source_revision.len() >= 7
            && self.source_revision.chars().all(|c| c.is_ascii_hexdigit())
    }

    /// Returns true if artifact hashes are non-empty (required for observed evidence).
    pub fn has_artifact_hashes(&self) -> bool {
        !self.result_artifact_sha256.is_empty() && !self.logs_artifact_sha256.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_envelope() -> EvidenceEnvelope {
        EvidenceEnvelope {
            evidence_version: "1.0.0".into(),
            evidence_id: uuid::Uuid::new_v4().to_string(),
            work_package_id: "FND-03".into(),
            requirement_ids: vec!["NR-SPEC-002".into()],
            test_ids: vec!["T-SPEC-001".into()],
            status: EnvelopeStatus::ObservedPass,
            source_revision: "5a24249a9098a6c468da45d27a449fab380863b5".into(),
            artifact_digests: HashMap::from([("container".into(), "sha256:abc123".into())]),
            environment_profile: "single-region-org-v1-local-test".into(),
            command: "cargo test --workspace".into(),
            started_at: "2026-08-21T14:00:00Z".into(),
            finished_at: "2026-08-21T14:05:00Z".into(),
            runner_identity: "ROLE-RUST".into(),
            independent_verifier_identity: Some("ROLE-VERIFY".into()),
            result_artifact_sha256:
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855".into(),
            logs_artifact_sha256:
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855".into(),
            exceptions: vec![],
            notes: String::new(),
        }
    }

    #[test]
    fn observed_pass_is_release_ready() {
        let env = valid_envelope();
        assert!(env.is_release_ready());
    }

    #[test]
    fn accepted_is_release_ready() {
        let mut env = valid_envelope();
        env.status = EnvelopeStatus::Accepted;
        assert!(env.is_release_ready());
    }

    #[test]
    fn claimed_is_not_release_ready() {
        let mut env = valid_envelope();
        env.status = EnvelopeStatus::Claimed;
        assert!(!env.is_release_ready());
    }

    #[test]
    fn observed_fail_is_not_release_ready() {
        let mut env = valid_envelope();
        env.status = EnvelopeStatus::ObservedFail;
        assert!(!env.is_release_ready());
    }

    #[test]
    fn inferred_is_not_release_ready() {
        let mut env = valid_envelope();
        env.status = EnvelopeStatus::Inferred;
        assert!(!env.is_release_ready());
    }

    #[test]
    fn contradicted_is_not_release_ready() {
        let mut env = valid_envelope();
        env.status = EnvelopeStatus::Contradicted;
        assert!(!env.is_release_ready());
    }

    #[test]
    fn rejected_is_not_release_ready() {
        let mut env = valid_envelope();
        env.status = EnvelopeStatus::Rejected;
        assert!(!env.is_release_ready());
    }

    #[test]
    fn valid_sha_passes_revision_check() {
        let env = valid_envelope();
        assert!(env.has_valid_revision());
    }

    #[test]
    fn empty_revision_fails() {
        let mut env = valid_envelope();
        env.source_revision = String::new();
        assert!(!env.has_valid_revision());
    }

    #[test]
    fn short_revision_passes() {
        let mut env = valid_envelope();
        env.source_revision = "5a24249".into();
        assert!(env.has_valid_revision());
    }

    #[test]
    fn non_hex_revision_fails() {
        let mut env = valid_envelope();
        env.source_revision = "not-a-sha-xyz".into();
        assert!(!env.has_valid_revision());
    }

    #[test]
    fn has_artifact_hashes_when_both_present() {
        let env = valid_envelope();
        assert!(env.has_artifact_hashes());
    }

    #[test]
    fn missing_result_hash_fails_artifact_check() {
        let mut env = valid_envelope();
        env.result_artifact_sha256 = String::new();
        assert!(!env.has_artifact_hashes());
    }

    #[test]
    fn missing_logs_hash_fails_artifact_check() {
        let mut env = valid_envelope();
        env.logs_artifact_sha256 = String::new();
        assert!(!env.has_artifact_hashes());
    }

    #[test]
    fn envelope_serializes_correctly() {
        let env = valid_envelope();
        let json = serde_json::to_string_pretty(&env).unwrap();
        let parsed: EvidenceEnvelope = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.evidence_version, "1.0.0");
        assert_eq!(parsed.status, EnvelopeStatus::ObservedPass);
        assert_eq!(parsed.work_package_id, "FND-03");
    }

    #[test]
    fn envelope_without_verifier_serializes_null() {
        let mut env = valid_envelope();
        env.independent_verifier_identity = None;
        let json = serde_json::to_string_pretty(&env).unwrap();
        let parsed: EvidenceEnvelope = serde_json::from_str(&json).unwrap();
        assert!(parsed.independent_verifier_identity.is_none());
    }
}
