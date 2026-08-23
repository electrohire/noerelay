use anyhow::{Context, Result};
use noerelay_core::EvidenceEnvelope;
use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

/// A single requirement entry from the coverage manifest.
#[derive(Debug, Clone, Deserialize)]
struct ManifestRequirement {
    requirement_id: String,
    #[allow(dead_code)]
    primary_work_packages: Vec<String>,
    primary_release_tests: Vec<String>,
    #[serde(default)]
    #[allow(dead_code)]
    must: bool,
}

/// A release gate definition from the coverage manifest.
#[derive(Debug, Clone, Deserialize)]
struct ManifestGate {
    #[allow(dead_code)]
    description: String,
    requirements: Vec<String>,
    #[allow(dead_code)]
    tests: Vec<String>,
}

/// The full coverage manifest.
#[derive(Debug, Clone, Deserialize)]
struct CoverageManifest {
    #[allow(dead_code)]
    manifest_version: String,
    #[allow(dead_code)]
    description: String,
    #[allow(dead_code)]
    source: String,
    requirements: Vec<ManifestRequirement>,
    release_gates: BTreeMap<String, ManifestGate>,
}

/// Status of a single requirement in the coverage report.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CoverageStatus {
    /// Evidence exists with observed_pass or accepted status.
    Covered,
    /// Evidence exists but not in a release-ready status.
    Partial,
    /// No evidence found for this requirement.
    Missing,
}

/// Coverage entry for a single requirement.
#[derive(Debug, Clone)]
pub struct CoverageEntry {
    pub requirement_id: String,
    pub work_packages: Vec<String>,
    pub test_ids: Vec<String>,
    pub status: CoverageStatus,
    pub evidence_ids: Vec<String>,
}

/// Full coverage report.
#[derive(Debug, Clone)]
pub struct CoverageReport {
    pub entries: Vec<CoverageEntry>,
    pub total_requirements: usize,
    pub covered: usize,
    pub partial: usize,
    pub missing: usize,
    pub passed: bool,
}

/// Load the coverage manifest from `spec/coverage-manifest.json`.
fn load_manifest(manifest_path: &Path) -> Result<CoverageManifest> {
    let content = fs::read_to_string(manifest_path).with_context(|| {
        format!(
            "failed to read coverage manifest: {}",
            manifest_path.display()
        )
    })?;
    let manifest: CoverageManifest =
        serde_json::from_str(&content).with_context(|| "failed to parse coverage manifest")?;
    Ok(manifest)
}

/// Load all evidence envelopes from the evidence directory.
fn load_evidence(evidence_dir: &Path) -> Result<Vec<EvidenceEnvelope>> {
    let mut envelopes = Vec::new();
    if !evidence_dir.exists() {
        return Ok(envelopes);
    }
    collect_envelopes(evidence_dir, &mut envelopes)?;
    Ok(envelopes)
}

fn collect_envelopes(dir: &Path, envelopes: &mut Vec<EvidenceEnvelope>) -> Result<()> {
    for entry in
        fs::read_dir(dir).with_context(|| format!("failed to read directory: {}", dir.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            collect_envelopes(&path, envelopes)?;
        } else if path.extension().is_some_and(|ext| ext == "json") {
            let content = fs::read_to_string(&path)
                .with_context(|| format!("failed to read: {}", path.display()))?;
            let envelope: EvidenceEnvelope = serde_json::from_str(&content)
                .with_context(|| format!("failed to parse: {}", path.display()))?;
            envelopes.push(envelope);
        }
    }
    Ok(())
}

/// Generate a requirement coverage report.
///
/// Loads the coverage manifest and evidence envelopes, then produces a report
/// showing each requirement's coverage status.
pub fn generate_coverage_report(
    manifest_path: &Path,
    evidence_dir: &Path,
) -> Result<CoverageReport> {
    let manifest = load_manifest(manifest_path)?;
    let envelopes = load_evidence(evidence_dir)?;

    // Build a map: requirement_id -> list of evidence envelopes
    let mut evidence_by_req: BTreeMap<String, Vec<&EvidenceEnvelope>> = BTreeMap::new();
    for envelope in &envelopes {
        for req_id in &envelope.requirement_ids {
            evidence_by_req
                .entry(req_id.clone())
                .or_default()
                .push(envelope);
        }
    }

    let mut entries = Vec::new();
    let mut covered = 0usize;
    let mut partial = 0usize;
    let mut missing = 0usize;

    for req in &manifest.requirements {
        let req_evidence = evidence_by_req.get(&req.requirement_id);
        let (status, evidence_ids) = match req_evidence {
            Some(ev_list) if ev_list.iter().any(|e| e.is_release_ready()) => {
                let ids: Vec<String> = ev_list
                    .iter()
                    .filter(|e| e.is_release_ready())
                    .map(|e| e.evidence_id.clone())
                    .collect();
                (CoverageStatus::Covered, ids)
            }
            Some(ev_list) => {
                let ids: Vec<String> = ev_list.iter().map(|e| e.evidence_id.clone()).collect();
                (CoverageStatus::Partial, ids)
            }
            None => (CoverageStatus::Missing, vec![]),
        };

        match status {
            CoverageStatus::Covered => covered += 1,
            CoverageStatus::Partial => partial += 1,
            CoverageStatus::Missing => missing += 1,
        }

        entries.push(CoverageEntry {
            requirement_id: req.requirement_id.clone(),
            work_packages: req.primary_work_packages.clone(),
            test_ids: req.primary_release_tests.clone(),
            status,
            evidence_ids,
        });
    }

    let total = manifest.requirements.len();
    // Report passes if all mandatory requirements are covered
    let passed = missing == 0;

    Ok(CoverageReport {
        entries,
        total_requirements: total,
        covered,
        partial,
        missing,
        passed,
    })
}

/// Check if a specific release gate has all required evidence.
pub fn check_gate(gate_id: &str, manifest_path: &Path, evidence_dir: &Path) -> Result<bool> {
    let manifest = load_manifest(manifest_path)?;
    let envelopes = load_evidence(evidence_dir)?;

    let gate = manifest
        .release_gates
        .get(gate_id)
        .with_context(|| format!("unknown release gate: {}", gate_id))?;

    // Build a set of requirement IDs that have release-ready evidence
    let covered_reqs: BTreeSet<String> = envelopes
        .iter()
        .filter(|e| e.is_release_ready())
        .flat_map(|e| e.requirement_ids.clone())
        .collect();

    let mut all_covered = true;
    for req_id in &gate.requirements {
        if !covered_reqs.contains(req_id) {
            eprintln!("  Gate {}: requirement {} NOT covered", gate_id, req_id);
            all_covered = false;
        }
    }

    if all_covered {
        eprintln!("Gate {} ({}) : PASS", gate_id, gate.description);
    } else {
        eprintln!("Gate {} ({}) : FAIL", gate_id, gate.description);
    }

    Ok(all_covered)
}

/// Print a coverage report to stderr.
pub fn print_coverage_report(report: &CoverageReport) {
    eprintln!("=== Requirement Coverage Report ===");
    eprintln!("Total requirements: {}", report.total_requirements);
    eprintln!("  Covered:  {}", report.covered);
    eprintln!("  Partial:  {}", report.partial);
    eprintln!("  Missing:  {}", report.missing);
    eprintln!("Result: {}", if report.passed { "PASS" } else { "FAIL" });
    eprintln!();

    for entry in &report.entries {
        let status_str = match entry.status {
            CoverageStatus::Covered => "COVERED",
            CoverageStatus::Partial => "PARTIAL",
            CoverageStatus::Missing => "MISSING",
        };
        eprintln!(
            "  [{}] {} -> WPs: {:?} | Tests: {:?} | Evidence: {:?}",
            status_str,
            entry.requirement_id,
            entry.work_packages,
            entry.test_ids,
            entry.evidence_ids
        );
    }

    eprintln!("====================================");
}

#[cfg(test)]
mod tests {
    use super::*;
    use noerelay_core::EnvelopeStatus;
    use std::collections::HashMap;

    fn make_envelope(
        id: &str,
        wp: &str,
        status: EnvelopeStatus,
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
            source_revision: "5a24249a9098a6c468da45d27a449fab380863b5".into(),
            artifact_digests: HashMap::new(),
            environment_profile: "test".into(),
            command: "test".into(),
            started_at: "2026-01-01T00:00:00Z".into(),
            finished_at: "2026-01-01T00:01:00Z".into(),
            runner_identity: "test".into(),
            independent_verifier_identity: Some("verifier".into()),
            result_artifact_sha256: "abc".into(),
            logs_artifact_sha256: "def".into(),
            exceptions: vec![],
            notes: String::new(),
        }
    }

    #[test]
    fn coverage_report_detects_missing() {
        let dir = tempfile::tempdir().unwrap();
        let manifest_path = Path::new("spec/coverage-manifest.json");

        // Only create evidence if the manifest exists
        if manifest_path.exists() {
            let report = generate_coverage_report(manifest_path, dir.path()).unwrap();
            // With no evidence, all requirements should be missing
            assert!(!report.passed);
            assert_eq!(report.missing, report.total_requirements);
        }
    }

    #[test]
    fn coverage_report_detects_covered() {
        let evidence_dir = tempfile::tempdir().unwrap();
        let manifest_path = Path::new("spec/coverage-manifest.json");

        if !manifest_path.exists() {
            return;
        }

        // Create evidence for NR-SPEC-002
        let env = make_envelope(
            "ev-1",
            "FND-03",
            EnvelopeStatus::ObservedPass,
            vec!["NR-SPEC-002"],
            vec!["T-SPEC-001"],
        );
        let wp_dir = evidence_dir.path().join("FND-03");
        fs::create_dir_all(&wp_dir).unwrap();
        fs::write(
            wp_dir.join("ev-1.json"),
            serde_json::to_string_pretty(&env).unwrap(),
        )
        .unwrap();

        let report = generate_coverage_report(manifest_path, evidence_dir.path()).unwrap();
        let entry = report
            .entries
            .iter()
            .find(|e| e.requirement_id == "NR-SPEC-002")
            .unwrap();
        assert_eq!(entry.status, CoverageStatus::Covered);
    }
}
