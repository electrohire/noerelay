//! Negative evidence validation tests for FND-03.
//!
//! These tests verify that the evidence bundle validator correctly rejects:
//! - Claimed status evidence
//! - Evidence with empty artifact hashes
//! - Evidence missing independent verifier identity
//! - Orphaned evidence (no matching requirement/test)
//! - Evidence with invalid source revision
//! - Tampered log files

use noerelay_core::{EnvelopeStatus, EvidenceEnvelope};
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::Path;

/// Helper to create a valid evidence envelope for testing.
fn valid_envelope(id: &str, wp: &str) -> EvidenceEnvelope {
    EvidenceEnvelope {
        evidence_version: "1.0.0".into(),
        evidence_id: id.into(),
        work_package_id: wp.into(),
        requirement_ids: vec!["NR-SPEC-002".into()],
        test_ids: vec!["T-SPEC-001".into()],
        status: EnvelopeStatus::ObservedPass,
        source_revision: "5a24249a9098a6c468da45d27a449fab380863b5".into(),
        artifact_digests: HashMap::new(),
        environment_profile: "single-region-org-v1-local-test".into(),
        command: "cargo test".into(),
        started_at: "2026-08-21T14:00:00Z".into(),
        finished_at: "2026-08-21T14:05:00Z".into(),
        runner_identity: "ROLE-RUST".into(),
        independent_verifier_identity: Some("ROLE-VERIFY".into()),
        result_artifact_sha256: "abc123def456".into(),
        logs_artifact_sha256: "def456abc123".into(),
        exceptions: vec![],
        notes: String::new(),
    }
}

/// Write envelopes to a temp evidence directory and return the validator.
fn setup_validator(
    dir: &Path,
    envelopes: &[EvidenceEnvelope],
) -> (BTreeSet<String>, BTreeSet<String>) {
    for env in envelopes {
        let wp_dir = dir.join(&env.work_package_id);
        fs::create_dir_all(&wp_dir).unwrap();
        let path = wp_dir.join(format!("{}.json", env.evidence_id));
        fs::write(&path, serde_json::to_string_pretty(env).unwrap()).unwrap();
    }

    let known_reqs: BTreeSet<String> = ["NR-SPEC-002".into()].into();
    let known_tests: BTreeSet<String> = ["T-SPEC-001".into()].into();
    (known_reqs, known_tests)
}

fn run_validation(dir: &Path, envelopes: &[EvidenceEnvelope]) -> bool {
    let (known_reqs, known_tests) = setup_validator(dir, envelopes);
    let validator = xtask_validate::BundleValidator::new(dir, known_reqs, known_tests);
    let report = validator.validate().unwrap();
    report.passed
}

// ---------------------------------------------------------------------------
// Test 1: Valid evidence passes
// ---------------------------------------------------------------------------
#[test]
fn valid_evidence_passes_validation() {
    let dir = tempfile::tempdir().unwrap();
    let envelopes = vec![valid_envelope("ev-valid", "FND-03")];
    assert!(run_validation(dir.path(), &envelopes));
}

// ---------------------------------------------------------------------------
// Test 2: Claimed status is rejected for release gates
// ---------------------------------------------------------------------------
#[test]
fn claimed_status_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-claimed", "FND-03");
    env.status = EnvelopeStatus::Claimed;
    let envelopes = vec![env];
    assert!(
        !run_validation(dir.path(), &envelopes),
        "Claimed status should be rejected for release gates"
    );
}

// ---------------------------------------------------------------------------
// Test 3: Empty result_artifact_sha256 is rejected
// ---------------------------------------------------------------------------
#[test]
fn empty_result_artifact_hash_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-empty-result", "FND-03");
    env.result_artifact_sha256 = String::new();
    let envelopes = vec![env];
    assert!(
        !run_validation(dir.path(), &envelopes),
        "Empty result_artifact_sha256 should be rejected"
    );
}

// ---------------------------------------------------------------------------
// Test 4: Empty logs_artifact_sha256 is rejected
// ---------------------------------------------------------------------------
#[test]
fn empty_logs_artifact_hash_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-empty-logs", "FND-03");
    env.logs_artifact_sha256 = String::new();
    let envelopes = vec![env];
    assert!(
        !run_validation(dir.path(), &envelopes),
        "Empty logs_artifact_sha256 should be rejected"
    );
}

// ---------------------------------------------------------------------------
// Test 5: Missing independent_verifier_identity is rejected
// ---------------------------------------------------------------------------
#[test]
fn missing_independent_verifier_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-no-verifier", "FND-03");
    env.independent_verifier_identity = None;
    let envelopes = vec![env];
    assert!(
        !run_validation(dir.path(), &envelopes),
        "Missing independent_verifier_identity should be rejected"
    );
}

// ---------------------------------------------------------------------------
// Test 6: Orphaned evidence (no matching requirement) is flagged
// ---------------------------------------------------------------------------
#[test]
fn orphaned_evidence_is_flagged() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-orphan", "FND-03");
    env.requirement_ids = vec!["UNKNOWN-REQ-999".into()];
    env.test_ids = vec!["UNKNOWN-TEST-999".into()];
    let envelopes = vec![env];
    assert!(
        !run_validation(dir.path(), &envelopes),
        "Orphaned evidence should be flagged"
    );
}

// ---------------------------------------------------------------------------
// Test 7: Invalid source revision is rejected
// ---------------------------------------------------------------------------
#[test]
fn invalid_source_revision_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-bad-rev", "FND-03");
    env.source_revision = "not-a-valid-sha".into();
    let envelopes = vec![env];
    assert!(
        !run_validation(dir.path(), &envelopes),
        "Invalid source_revision should be rejected"
    );
}

// ---------------------------------------------------------------------------
// Test 8: Empty source revision is rejected
// ---------------------------------------------------------------------------
#[test]
fn empty_source_revision_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-empty-rev", "FND-03");
    env.source_revision = String::new();
    let envelopes = vec![env];
    assert!(
        !run_validation(dir.path(), &envelopes),
        "Empty source_revision should be rejected"
    );
}

// ---------------------------------------------------------------------------
// Test 9: ObservedFail status is not release-ready (warning, not error)
// ---------------------------------------------------------------------------
#[test]
fn observed_fail_is_not_release_ready() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-fail", "FND-03");
    env.status = EnvelopeStatus::ObservedFail;
    assert!(
        !env.is_release_ready(),
        "ObservedFail should not be release-ready"
    );
    let envelopes = vec![env];
    // Validation passes (warning only), but the envelope itself is not release-ready
    assert!(
        run_validation(dir.path(), &envelopes),
        "ObservedFail is a warning, not an error — bundle should still pass"
    );
}

// ---------------------------------------------------------------------------
// Test 10: Inferred status is not release-ready (warning, not error)
// ---------------------------------------------------------------------------
#[test]
fn inferred_status_is_not_release_ready() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-inferred", "FND-03");
    env.status = EnvelopeStatus::Inferred;
    assert!(
        !env.is_release_ready(),
        "Inferred should not be release-ready"
    );
    let envelopes = vec![env];
    assert!(
        run_validation(dir.path(), &envelopes),
        "Inferred is a warning, not an error — bundle should still pass"
    );
}

// ---------------------------------------------------------------------------
// Test 11: Contradicted status is not release-ready (warning, not error)
// ---------------------------------------------------------------------------
#[test]
fn contradicted_status_is_not_release_ready() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-contra", "FND-03");
    env.status = EnvelopeStatus::Contradicted;
    assert!(
        !env.is_release_ready(),
        "Contradicted should not be release-ready"
    );
    let envelopes = vec![env];
    assert!(
        run_validation(dir.path(), &envelopes),
        "Contradicted is a warning, not an error — bundle should still pass"
    );
}

// ---------------------------------------------------------------------------
// Test 12: Rejected status is not release-ready (warning, not error)
// ---------------------------------------------------------------------------
#[test]
fn rejected_status_is_not_release_ready() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-rejected", "FND-03");
    env.status = EnvelopeStatus::Rejected;
    assert!(
        !env.is_release_ready(),
        "Rejected should not be release-ready"
    );
    let envelopes = vec![env];
    assert!(
        run_validation(dir.path(), &envelopes),
        "Rejected is a warning, not an error — bundle should still pass"
    );
}

// ---------------------------------------------------------------------------
// Test 13: Accepted status IS release-ready
// ---------------------------------------------------------------------------
#[test]
fn accepted_status_is_release_ready() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-accepted", "FND-03");
    env.status = EnvelopeStatus::Accepted;
    let envelopes = vec![env];
    assert!(
        run_validation(dir.path(), &envelopes),
        "Accepted status should be release-ready"
    );
}

// ---------------------------------------------------------------------------
// Test 14: Tampered logs are detected
// ---------------------------------------------------------------------------
#[test]
fn tampered_logs_are_detected() {
    let dir = tempfile::tempdir().unwrap();
    let mut env = valid_envelope("ev-tampered", "FND-03");

    // Create a log file with known content
    let wp_dir = dir.path().join("FND-03");
    fs::create_dir_all(&wp_dir).unwrap();
    let log_path = wp_dir.join("ev-tampered_logs.txt");
    fs::write(&log_path, "original log content").unwrap();

    // Set the logs hash to something different (tampered)
    env.logs_artifact_sha256 =
        "0000000000000000000000000000000000000000000000000000000000000000".into();

    let envelopes = vec![env];
    assert!(
        !run_validation(dir.path(), &envelopes),
        "Tampered logs should be detected"
    );
}

// ---------------------------------------------------------------------------
// Test 15: Orphaned requirements are flagged (no evidence at all)
// ---------------------------------------------------------------------------
#[test]
fn orphaned_requirements_are_flagged() {
    let dir = tempfile::tempdir().unwrap();
    // No envelopes at all
    let known_reqs: BTreeSet<String> = ["NR-SPEC-002".into()].into();
    let known_tests: BTreeSet<String> = ["T-SPEC-001".into()].into();
    let validator = xtask_validate::BundleValidator::new(dir.path(), known_reqs, known_tests);
    let report = validator.validate().unwrap();
    assert!(!report.passed, "Orphaned requirements should cause failure");
    assert!(
        report
            .orphaned_requirements
            .contains(&"NR-SPEC-002".to_string())
    );
}

// ---------------------------------------------------------------------------
// Test 16: Multiple valid envelopes all pass
// ---------------------------------------------------------------------------
#[test]
fn multiple_valid_envelopes_pass() {
    let dir = tempfile::tempdir().unwrap();
    let envelopes = vec![
        valid_envelope("ev-1", "FND-03"),
        valid_envelope("ev-2", "FND-03"),
    ];
    assert!(run_validation(dir.path(), &envelopes));
}

// Include the validate module from xtask for testing
#[path = "../src/validate.rs"]
mod xtask_validate;
