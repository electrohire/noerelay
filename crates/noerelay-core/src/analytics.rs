//! Analytics projector contract per mission §11.
//!
//! Defines the interface for rebuildable, idempotent analytics projections
//! that consume verified ledger events without affecting production routing.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// A projector that consumes ledger events and produces analytics projections.
///
/// Projectors verify chain continuity and signatures before consuming events.
/// Each projection stores source ledger metadata for rebuildability.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ProjectorMetadata {
    /// Source ledger partition identifier.
    pub source_ledger_id: String,
    /// Starting sequence number of the consumed range.
    pub sequence_start: u64,
    /// Ending sequence number of the consumed range (inclusive).
    pub sequence_end: u64,
    /// Ledger head hash at the time of projection.
    pub head_hash: String,
    /// Projector implementation revision.
    pub projector_revision: String,
    /// Projection schema revision.
    pub schema_revision: String,
    /// ISO 8601 timestamp of the projection build.
    pub rebuilt_at: String,
}

/// A versioned metric definition per mission §11.3.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct MetricDefinition {
    /// Unique metric identifier.
    pub metric_id: String,
    /// Human-readable metric name.
    pub name: String,
    /// Metric schema version.
    pub version: String,
    /// Numerator description.
    pub numerator: String,
    /// Denominator description.
    pub denominator: String,
    /// Exclusions from the metric.
    #[serde(default)]
    pub exclusions: Vec<String>,
    /// Time basis (e.g., "per_run", "per_hour", "per_day").
    pub time_basis: String,
    /// Dimensions for slicing.
    #[serde(default)]
    pub dimensions: Vec<String>,
    /// Source ledger event types.
    #[serde(default)]
    pub source_event_types: Vec<String>,
}

/// Core metrics defined by the mission (§11.3).
pub mod core_metrics {
    use super::MetricDefinition;

    pub fn routing_volume() -> MetricDefinition {
        MetricDefinition {
            metric_id: "routing.volume".into(),
            name: "Routing Volume".into(),
            version: "1.0.0".into(),
            numerator: "Count of route decisions".into(),
            denominator: "1".into(),
            exclusions: vec![],
            time_basis: "per_hour".into(),
            dimensions: vec!["cohort".into(), "provider".into(), "mode".into()],
            source_event_types: vec!["route_selected".into()],
        }
    }

    pub fn verified_acceptance_rate() -> MetricDefinition {
        MetricDefinition {
            metric_id: "quality.acceptance_rate".into(),
            name: "Verified Acceptance Rate".into(),
            version: "1.0.0".into(),
            numerator: "Count of accepted releases".into(),
            denominator: "Count of completed runs".into(),
            exclusions: vec!["aborted_runs".into()],
            time_basis: "per_day".into(),
            dimensions: vec!["cohort".into(), "risk_class".into()],
            source_event_types: vec!["run_released".into(), "run_rejected".into()],
        }
    }

    pub fn ranker_follow_rate() -> MetricDefinition {
        MetricDefinition {
            metric_id: "ranker.follow_rate".into(),
            name: "Ranker Follow Rate".into(),
            version: "1.0.0".into(),
            numerator: "Count of routes where advice was followed".into(),
            denominator: "Count of routes where advice was received".into(),
            exclusions: vec!["disabled_mode".into(), "shadow_mode".into()],
            time_basis: "per_day".into(),
            dimensions: vec!["ranker_id".into(), "cohort".into()],
            source_event_types: vec!["route_selected".into()],
        }
    }

    pub fn cost_per_accepted_result() -> MetricDefinition {
        MetricDefinition {
            metric_id: "cost.per_accepted_result".into(),
            name: "Cost Per Accepted Result".into(),
            version: "1.0.0".into(),
            numerator: "Sum of actual cost micro-USD".into(),
            denominator: "Count of accepted releases".into(),
            exclusions: vec![],
            time_basis: "per_day".into(),
            dimensions: vec!["provider".into(), "model".into(), "cohort".into()],
            source_event_types: vec!["cost_reconciled".into(), "run_released".into()],
        }
    }

    pub fn evidence_completeness() -> MetricDefinition {
        MetricDefinition {
            metric_id: "evidence.completeness".into(),
            name: "Evidence Completeness".into(),
            version: "1.0.0".into(),
            numerator: "Count of runs with observed evidence for all required gates".into(),
            denominator: "Count of completed runs".into(),
            exclusions: vec![],
            time_basis: "per_day".into(),
            dimensions: vec!["risk_class".into(), "cohort".into()],
            source_event_types: vec![
                "verification_observed".into(),
                "claim_transitioned".into(),
            ],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn projector_metadata_serializes() {
        let meta = ProjectorMetadata {
            source_ledger_id: "org/project".into(),
            sequence_start: 1,
            sequence_end: 100,
            head_hash: "abc123".into(),
            projector_revision: "v1.0.0".into(),
            schema_revision: "v1.0.0".into(),
            rebuilt_at: "2026-09-03T00:00:00Z".into(),
        };
        let json = serde_json::to_string(&meta).unwrap();
        assert!(json.contains("\"source_ledger_id\":\"org/project\""));
        assert!(json.contains("\"sequence_start\":1"));
    }

    #[test]
    fn all_core_metrics_have_required_fields() {
        let metrics = [
            core_metrics::routing_volume(),
            core_metrics::verified_acceptance_rate(),
            core_metrics::ranker_follow_rate(),
            core_metrics::cost_per_accepted_result(),
            core_metrics::evidence_completeness(),
        ];
        for m in &metrics {
            assert!(!m.metric_id.is_empty());
            assert!(!m.name.is_empty());
            assert!(!m.version.is_empty());
            assert!(!m.numerator.is_empty());
            assert!(!m.denominator.is_empty());
        }
    }
}