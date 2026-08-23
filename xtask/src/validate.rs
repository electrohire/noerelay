use anyhow::{Context, Result};
use noerelay_core::{EnvelopeStatus, EvidenceEnvelope};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

/// A single issue found during evidence bundle validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidationIssue {
    /// Severity: "error" or "warning".
    pub severity: String,
    /// Human-readable description of the issue.
    pub message: String,
    /// The evidence ID this issue relates to, if applicable.
    pub evidence_id: Option<String>,
}

/// Result of validating an evidence bundle.
#[derive(Debug, Clone)]
pub struct ValidationReport {
    /// Whether the bundle passed validation (no errors).
    pub passed: bool,
    /// All issues found, both errors and warnings.
    pub issues: Vec<ValidationIssue>,
    /// Count of evidence envelopes loaded.
    #[allow(dead_code)]
    pub total_evidence: usize,
    /// Count of evidence envelopes that are release-ready.
    #[allow(dead_code)]
    pub release_ready: usize,
    /// Orphaned requirement IDs (no evidence found).
    pub orphaned_requirements: Vec<String>,
    /// Orphaned test IDs (no evidence found).
    #[allow(dead_code)]
    pub orphaned_tests: Vec<String>,
    /// Orphaned evidence IDs (no matching requirement/test).
    pub orphaned_evidence: Vec<String>,
}

/// Validates evidence bundles in the `evidence/` directory.
pub struct BundleValidator {
    evidence_dir: PathBuf,
    /// Known requirement IDs for orphan detection.
    known_requirements: BTreeSet<String>,
    /// Known test IDs for orphan detection.
    known_tests: BTreeSet<String>,
    /// Whether to require independent verifier identity.
    require_independent_verifier: bool,
}

impl BundleValidator {
    /// Create a new validator.
    pub fn new(
        evidence_dir: &Path,
        known_requirements: BTreeSet<String>,
        known_tests: BTreeSet<String>,
    ) -> Self {
        Self {
            evidence_dir: evidence_dir.to_path_buf(),
            known_requirements,
            known_tests,
            require_independent_verifier: true,
        }
    }

    /// Set whether independent verifier identity is required.
    #[allow(dead_code)]
    pub fn with_independent_verifier(mut self, require: bool) -> Self {
        self.require_independent_verifier = require;
        self
    }

    /// Load all evidence envelopes from the evidence directory.
    pub fn load_all(&self) -> Result<Vec<EvidenceEnvelope>> {
        let mut envelopes = Vec::new();

        if !self.evidence_dir.exists() {
            return Ok(envelopes);
        }

        self.collect_envelopes(&self.evidence_dir, &mut envelopes)?;
        Ok(envelopes)
    }

    fn collect_envelopes(&self, dir: &Path, envelopes: &mut Vec<EvidenceEnvelope>) -> Result<()> {
        for entry in fs::read_dir(dir)
            .with_context(|| format!("failed to read evidence directory: {}", dir.display()))?
        {
            let entry = entry?;
            let path = entry.path();
            if path.is_dir() {
                self.collect_envelopes(&path, envelopes)?;
            } else if path.extension().is_some_and(|ext| ext == "json") {
                let content = fs::read_to_string(&path)
                    .with_context(|| format!("failed to read: {}", path.display()))?;
                let envelope: EvidenceEnvelope =
                    serde_json::from_str(&content).with_context(|| {
                        format!("failed to parse evidence envelope: {}", path.display())
                    })?;
                envelopes.push(envelope);
            }
        }
        Ok(())
    }

    /// Validate all evidence bundles and return a report.
    pub fn validate(&self) -> Result<ValidationReport> {
        let envelopes = self.load_all()?;
        let mut issues = Vec::new();

        // Track which requirements and tests have evidence
        let mut evidenced_requirements: BTreeSet<String> = BTreeSet::new();
        let mut evidenced_tests: BTreeSet<String> = BTreeSet::new();
        let mut release_ready = 0usize;

        for envelope in &envelopes {
            // Check 1: Valid source revision
            if !envelope.has_valid_revision() {
                issues.push(ValidationIssue {
                    severity: "error".into(),
                    message: format!(
                        "Evidence {} has invalid source_revision: '{}'",
                        envelope.evidence_id, envelope.source_revision
                    ),
                    evidence_id: Some(envelope.evidence_id.clone()),
                });
            }

            // Check 2: Status must be observed_pass or accepted for release gates
            if !envelope.is_release_ready() {
                if envelope.status == EnvelopeStatus::Claimed {
                    issues.push(ValidationIssue {
                        severity: "error".into(),
                        message: format!(
                            "Evidence {} has status 'claimed' — claimed results cannot satisfy release gates",
                            envelope.evidence_id
                        ),
                        evidence_id: Some(envelope.evidence_id.clone()),
                    });
                } else {
                    issues.push(ValidationIssue {
                        severity: "warning".into(),
                        message: format!(
                            "Evidence {} has status '{:?}' — not release-ready",
                            envelope.evidence_id, envelope.status
                        ),
                        evidence_id: Some(envelope.evidence_id.clone()),
                    });
                }
            } else {
                release_ready += 1;
            }

            // Check 3: Artifact hashes must be non-empty for observed evidence
            if matches!(
                envelope.status,
                EnvelopeStatus::ObservedPass | EnvelopeStatus::ObservedFail
            ) && !envelope.has_artifact_hashes()
            {
                issues.push(ValidationIssue {
                    severity: "error".into(),
                    message: format!(
                        "Evidence {} has observed status but empty artifact hashes",
                        envelope.evidence_id
                    ),
                    evidence_id: Some(envelope.evidence_id.clone()),
                });
            }

            // Check 4: Independent verifier identity required
            if self.require_independent_verifier
                && envelope.is_release_ready()
                && envelope.independent_verifier_identity.is_none()
            {
                issues.push(ValidationIssue {
                    severity: "error".into(),
                    message: format!(
                        "Evidence {} is release-ready but missing independent_verifier_identity",
                        envelope.evidence_id
                    ),
                    evidence_id: Some(envelope.evidence_id.clone()),
                });
            }

            // Check 5: Tampered logs — re-compute SHA256 of log files if they exist
            if !envelope.logs_artifact_sha256.is_empty() {
                // Try to find a log file matching this evidence
                let log_path = self
                    .evidence_dir
                    .join(&envelope.work_package_id)
                    .join(format!("{}_logs.txt", envelope.evidence_id));
                if log_path.exists() {
                    match compute_sha256_file(&log_path) {
                        Ok(actual_hash) => {
                            if actual_hash != envelope.logs_artifact_sha256 {
                                issues.push(ValidationIssue {
                                    severity: "error".into(),
                                    message: format!(
                                        "Evidence {} has tampered logs: stored hash {} != computed hash {}",
                                        envelope.evidence_id,
                                        envelope.logs_artifact_sha256,
                                        actual_hash
                                    ),
                                    evidence_id: Some(envelope.evidence_id.clone()),
                                });
                            }
                        }
                        Err(e) => {
                            issues.push(ValidationIssue {
                                severity: "warning".into(),
                                message: format!(
                                    "Could not verify log integrity for {}: {}",
                                    envelope.evidence_id, e
                                ),
                                evidence_id: Some(envelope.evidence_id.clone()),
                            });
                        }
                    }
                }
            }

            // Track coverage
            for req_id in &envelope.requirement_ids {
                evidenced_requirements.insert(req_id.clone());
            }
            for test_id in &envelope.test_ids {
                evidenced_tests.insert(test_id.clone());
            }
        }

        // Check 6: Orphaned requirements (known but no evidence)
        let orphaned_requirements: Vec<String> = self
            .known_requirements
            .difference(&evidenced_requirements)
            .cloned()
            .collect();

        for req_id in &orphaned_requirements {
            issues.push(ValidationIssue {
                severity: "error".into(),
                message: format!("Requirement {} has no evidence", req_id),
                evidence_id: None,
            });
        }

        // Check 7: Orphaned tests (known but no evidence)
        let orphaned_tests: Vec<String> = self
            .known_tests
            .difference(&evidenced_tests)
            .cloned()
            .collect();

        for test_id in &orphaned_tests {
            issues.push(ValidationIssue {
                severity: "error".into(),
                message: format!("Test {} has no evidence", test_id),
                evidence_id: None,
            });
        }

        // Check 8: Orphaned evidence (evidence referencing unknown requirements/tests)
        let mut orphaned_evidence: Vec<String> = Vec::new();
        for envelope in &envelopes {
            let mut is_orphaned = true;
            for req_id in &envelope.requirement_ids {
                if self.known_requirements.contains(req_id) {
                    is_orphaned = false;
                    break;
                }
            }
            if is_orphaned {
                for test_id in &envelope.test_ids {
                    if self.known_tests.contains(test_id) {
                        is_orphaned = false;
                        break;
                    }
                }
            }
            if is_orphaned {
                orphaned_evidence.push(envelope.evidence_id.clone());
                issues.push(ValidationIssue {
                    severity: "error".into(),
                    message: format!(
                        "Evidence {} is orphaned — no matching requirement or test",
                        envelope.evidence_id
                    ),
                    evidence_id: Some(envelope.evidence_id.clone()),
                });
            }
        }

        let has_errors = issues.iter().any(|i| i.severity == "error");
        let passed = !has_errors;

        Ok(ValidationReport {
            passed,
            issues,
            total_evidence: envelopes.len(),
            release_ready,
            orphaned_requirements,
            orphaned_tests,
            orphaned_evidence,
        })
    }
}

/// Compute SHA-256 of a file, returning hex string.
fn compute_sha256_file(path: &Path) -> Result<String> {
    let mut file =
        fs::File::open(path).with_context(|| format!("failed to open: {}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 8192];
    loop {
        let n = file
            .read(&mut buffer)
            .with_context(|| format!("failed to read: {}", path.display()))?;
        if n == 0 {
            break;
        }
        hasher.update(&buffer[..n]);
    }
    Ok(hex::encode(hasher.finalize()))
}

/// Print a validation report to stderr in a human-readable format.
#[allow(dead_code)]
pub fn print_report(report: &ValidationReport) {
    eprintln!("=== Evidence Bundle Validation Report ===");
    eprintln!("Result: {}", if report.passed { "PASS" } else { "FAIL" });
    eprintln!("Total evidence envelopes: {}", report.total_evidence);
    eprintln!("Release-ready envelopes: {}", report.release_ready);
    eprintln!("Issues: {}", report.issues.len());

    if !report.orphaned_requirements.is_empty() {
        eprintln!(
            "Orphaned requirements (no evidence): {}",
            report.orphaned_requirements.len()
        );
        for req in &report.orphaned_requirements {
            eprintln!("  - {}", req);
        }
    }

    if !report.orphaned_tests.is_empty() {
        eprintln!(
            "Orphaned tests (no evidence): {}",
            report.orphaned_tests.len()
        );
        for test in &report.orphaned_tests {
            eprintln!("  - {}", test);
        }
    }

    if !report.orphaned_evidence.is_empty() {
        eprintln!(
            "Orphaned evidence (no matching requirement/test): {}",
            report.orphaned_evidence.len()
        );
        for ev in &report.orphaned_evidence {
            eprintln!("  - {}", ev);
        }
    }

    for issue in &report.issues {
        let prefix = if issue.severity == "error" {
            "ERROR"
        } else {
            "WARN"
        };
        if let Some(ref ev_id) = issue.evidence_id {
            eprintln!("  [{}] {} (evidence: {})", prefix, issue.message, ev_id);
        } else {
            eprintln!("  [{}] {}", prefix, issue.message);
        }
    }

    eprintln!("==========================================");
}

#[cfg(test)]
mod tests {
    use super::*;
    use noerelay_core::EnvelopeStatus;
    use std::collections::HashMap;

    #[allow(clippy::too_many_arguments)]
    fn make_envelope(
        id: &str,
        wp: &str,
        status: EnvelopeStatus,
        revision: &str,
        result_hash: &str,
        logs_hash: &str,
        verifier: Option<&str>,
        req_ids: Vec<&str>,
        test_ids: Vec<&str>,
    ) -> EvidenceEnvelope {
        EvidenceEnvelope {
            evidence_version: "1.0.0".into(),
            evidence_id: id.into(),
            work_package_id: wp.into(),
            requirement_ids: req_ids.into_iter().map(|s| s.to_string()).collect(),
            test_ids: test_ids.into_iter().map(|s| s.to_string()).collect(),
            status,
            source_revision: revision.into(),
            artifact_digests: HashMap::new(),
            environment_profile: "test".into(),
            command: "test".into(),
            started_at: "2026-01-01T00:00:00Z".into(),
            finished_at: "2026-01-01T00:01:00Z".into(),
            runner_identity: "test-runner".into(),
            independent_verifier_identity: verifier.map(|s| s.to_string()),
            result_artifact_sha256: result_hash.into(),
            logs_artifact_sha256: logs_hash.into(),
            exceptions: vec![],
            notes: String::new(),
        }
    }

    fn setup_validator(dir: &Path, envelopes: &[EvidenceEnvelope]) -> BundleValidator {
        for env in envelopes {
            let wp_dir = dir.join(&env.work_package_id);
            fs::create_dir_all(&wp_dir).unwrap();
            let path = wp_dir.join(format!("{}.json", env.evidence_id));
            fs::write(&path, serde_json::to_string_pretty(env).unwrap()).unwrap();
        }

        BundleValidator::new(
            dir,
            BTreeSet::from(["NR-SPEC-002".into()]),
            BTreeSet::from(["T-SPEC-001".into()]),
        )
    }

    #[test]
    fn valid_envelope_passes() {
        let dir = tempfile::tempdir().unwrap();
        let envelopes = vec![make_envelope(
            "ev-1",
            "FND-03",
            EnvelopeStatus::ObservedPass,
            "5a24249a9098a6c468da45d27a449fab380863b5",
            "abc123",
            "def456",
            Some("ROLE-VERIFY"),
            vec!["NR-SPEC-002"],
            vec!["T-SPEC-001"],
        )];
        let validator = setup_validator(dir.path(), &envelopes);
        let report = validator.validate().unwrap();
        assert!(
            report.passed,
            "Expected pass, got issues: {:?}",
            report.issues
        );
    }

    #[test]
    fn claimed_status_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let envelopes = vec![make_envelope(
            "ev-1",
            "FND-03",
            EnvelopeStatus::Claimed,
            "5a24249a9098a6c468da45d27a449fab380863b5",
            "abc123",
            "def456",
            Some("ROLE-VERIFY"),
            vec!["NR-SPEC-002"],
            vec!["T-SPEC-001"],
        )];
        let validator = setup_validator(dir.path(), &envelopes);
        let report = validator.validate().unwrap();
        assert!(!report.passed, "Expected failure for claimed status");
        assert!(
            report.issues.iter().any(|i| i.message.contains("claimed")),
            "Expected an issue about claimed status"
        );
    }

    #[test]
    fn empty_result_hash_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let envelopes = vec![make_envelope(
            "ev-1",
            "FND-03",
            EnvelopeStatus::ObservedPass,
            "5a24249a9098a6c468da45d27a449fab380863b5",
            "",
            "def456",
            Some("ROLE-VERIFY"),
            vec!["NR-SPEC-002"],
            vec!["T-SPEC-001"],
        )];
        let validator = setup_validator(dir.path(), &envelopes);
        let report = validator.validate().unwrap();
        assert!(!report.passed, "Expected failure for empty result hash");
        assert!(
            report
                .issues
                .iter()
                .any(|i| i.message.contains("empty artifact hashes")),
            "Expected an issue about empty artifact hashes"
        );
    }

    #[test]
    fn missing_independent_verifier_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let envelopes = vec![make_envelope(
            "ev-1",
            "FND-03",
            EnvelopeStatus::ObservedPass,
            "5a24249a9098a6c468da45d27a449fab380863b5",
            "abc123",
            "def456",
            None,
            vec!["NR-SPEC-002"],
            vec!["T-SPEC-001"],
        )];
        let validator = setup_validator(dir.path(), &envelopes);
        let report = validator.validate().unwrap();
        assert!(
            !report.passed,
            "Expected failure for missing independent verifier"
        );
        assert!(
            report
                .issues
                .iter()
                .any(|i| i.message.contains("independent_verifier_identity")),
            "Expected an issue about missing independent verifier"
        );
    }

    #[test]
    fn orphaned_evidence_is_flagged() {
        let dir = tempfile::tempdir().unwrap();
        let envelopes = vec![make_envelope(
            "ev-1",
            "FND-03",
            EnvelopeStatus::ObservedPass,
            "5a24249a9098a6c468da45d27a449fab380863b5",
            "abc123",
            "def456",
            Some("ROLE-VERIFY"),
            vec!["UNKNOWN-REQ"],
            vec!["UNKNOWN-TEST"],
        )];
        let validator = setup_validator(dir.path(), &envelopes);
        let report = validator.validate().unwrap();
        assert!(!report.passed, "Expected failure for orphaned evidence");
        assert!(!report.orphaned_evidence.is_empty());
    }

    #[test]
    fn orphaned_requirements_are_flagged() {
        let dir = tempfile::tempdir().unwrap();
        // No envelopes at all — requirements should be orphaned
        let validator = BundleValidator::new(
            dir.path(),
            BTreeSet::from(["NR-SPEC-002".into()]),
            BTreeSet::from(["T-SPEC-001".into()]),
        );
        let report = validator.validate().unwrap();
        assert!(!report.passed);
        assert!(
            report
                .orphaned_requirements
                .contains(&"NR-SPEC-002".to_string())
        );
    }

    #[test]
    fn invalid_revision_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let envelopes = vec![make_envelope(
            "ev-1",
            "FND-03",
            EnvelopeStatus::ObservedPass,
            "not-a-sha",
            "abc123",
            "def456",
            Some("ROLE-VERIFY"),
            vec!["NR-SPEC-002"],
            vec!["T-SPEC-001"],
        )];
        let validator = setup_validator(dir.path(), &envelopes);
        let report = validator.validate().unwrap();
        assert!(!report.passed, "Expected failure for invalid revision");
        assert!(
            report
                .issues
                .iter()
                .any(|i| i.message.contains("source_revision")),
            "Expected an issue about invalid source_revision"
        );
    }
}
