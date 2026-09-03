//! Tool execution boundary with RTK context compaction per mission §9.
//!
//! At each tool execution boundary, raw stdout/stderr and exit status are preserved
//! as immutable evidence. RTK-derived compact context is linked to raw evidence.
//! RTK failure falls back safely; missing raw evidence cannot satisfy high-risk
//! observed-evidence gates.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// A tool execution record preserving raw and compact evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ToolExecution {
    /// The exact command that was executed.
    pub command: String,
    /// Exit code of the process.
    pub exit_code: i32,
    /// Raw output artifact reference.
    pub raw_output: ArtifactRef,
    /// Compact (RTK-derived) output artifact reference.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub compact_output: Option<CompactArtifactRef>,
    /// RTK filter configuration used.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub filter: Option<RtkFilterInfo>,
    /// Token metrics for raw vs compact.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metrics: Option<TokenMetrics>,
}

/// Reference to an immutable content-addressed artifact.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ArtifactRef {
    /// Content-addressed artifact identifier.
    pub artifact_id: String,
    /// SHA-256 hash of the artifact content.
    pub sha256: String,
}

/// Reference to a compact (RTK-derived) artifact, linked to its raw source.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CompactArtifactRef {
    /// Content-addressed artifact identifier.
    pub artifact_id: String,
    /// SHA-256 hash of the compact content.
    pub sha256: String,
    /// The raw artifact this compact output was derived from.
    pub derived_from: String,
}

/// RTK filter configuration used for context compaction.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RtkFilterInfo {
    /// Filter name (e.g., "rtk").
    pub name: String,
    /// Filter version.
    pub version: String,
    /// SHA-256 hash of the filter configuration.
    pub configuration_hash: String,
}

/// Token metrics comparing raw and compact representations.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TokenMetrics {
    /// Estimated token count of raw output.
    pub raw_estimated_tokens: u64,
    /// Estimated token count of compact output.
    pub compact_estimated_tokens: u64,
}

impl ToolExecution {
    /// Returns true if raw evidence is present and can satisfy an observed-evidence gate.
    pub fn has_raw_evidence(&self) -> bool {
        !self.raw_output.artifact_id.is_empty() && !self.raw_output.sha256.is_empty()
    }

    /// Returns true if compact output is present and linked to raw evidence.
    pub fn has_valid_compact(&self) -> bool {
        self.compact_output
            .as_ref()
            .is_some_and(|c| c.derived_from == self.raw_output.artifact_id)
    }

    /// Returns true if the execution was successful (exit code 0).
    pub fn is_success(&self) -> bool {
        self.exit_code == 0
    }

    /// Returns the RTK reduction ratio if both raw and compact metrics are available.
    pub fn rtk_reduction_ratio(&self) -> Option<f64> {
        let m = self.metrics.as_ref()?;
        if m.raw_estimated_tokens == 0 {
            return None;
        }
        Some(m.compact_estimated_tokens as f64 / m.raw_estimated_tokens as f64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_execution() -> ToolExecution {
        ToolExecution {
            command: "cargo test --workspace --locked".into(),
            exit_code: 0,
            raw_output: ArtifactRef {
                artifact_id: "raw-001".into(),
                sha256: "abc123".into(),
            },
            compact_output: Some(CompactArtifactRef {
                artifact_id: "compact-001".into(),
                sha256: "def456".into(),
                derived_from: "raw-001".into(),
            }),
            filter: Some(RtkFilterInfo {
                name: "rtk".into(),
                version: "0.1.0".into(),
                configuration_hash: "cfg-hash".into(),
            }),
            metrics: Some(TokenMetrics {
                raw_estimated_tokens: 18_420,
                compact_estimated_tokens: 1_910,
            }),
        }
    }

    #[test]
    fn has_raw_evidence_when_both_present() {
        assert!(test_execution().has_raw_evidence());
    }

    #[test]
    fn missing_raw_sha256_fails_evidence_check() {
        let mut exec = test_execution();
        exec.raw_output.sha256 = String::new();
        assert!(!exec.has_raw_evidence());
    }

    #[test]
    fn valid_compact_links_to_raw() {
        assert!(test_execution().has_valid_compact());
    }

    #[test]
    fn compact_with_wrong_derived_from_is_invalid() {
        let mut exec = test_execution();
        exec.compact_output.as_mut().unwrap().derived_from = "wrong".into();
        assert!(!exec.has_valid_compact());
    }

    #[test]
    fn rtk_reduction_ratio_is_correct() {
        let ratio = test_execution().rtk_reduction_ratio().unwrap();
        assert!((ratio - 0.1037).abs() < 0.001); // 1910/18420 ≈ 0.1037
    }

    #[test]
    fn rtk_reduction_ratio_none_when_no_metrics() {
        let mut exec = test_execution();
        exec.metrics = None;
        assert_eq!(exec.rtk_reduction_ratio(), None);
    }

    #[test]
    fn is_success_for_zero_exit_code() {
        assert!(test_execution().is_success());
    }

    #[test]
    fn is_not_success_for_nonzero_exit_code() {
        let mut exec = test_execution();
        exec.exit_code = 1;
        assert!(!exec.is_success());
    }
}
