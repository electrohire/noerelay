use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CostBreakdown {
    pub inference_microusd: u64,
    pub tools_microusd: u64,
    pub verification_microusd: u64,
    pub expected_retry_microusd: u64,
    pub expected_fallback_microusd: u64,
    pub infrastructure_microusd: u64,
    pub expected_human_review_microusd: u64,
}

impl CostBreakdown {
    pub fn total_microusd(&self) -> Option<u64> {
        [
            self.inference_microusd,
            self.tools_microusd,
            self.verification_microusd,
            self.expected_retry_microusd,
            self.expected_fallback_microusd,
            self.infrastructure_microusd,
            self.expected_human_review_microusd,
        ]
        .into_iter()
        .try_fold(0_u64, u64::checked_add)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct UsageDimensions {
    pub organization_id: String,
    pub project_id: String,
    pub environment_id: String,
    pub user_id: String,
    pub api_key_id: String,
    pub model_id: String,
    pub agent_id: Option<String>,
    pub tool_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct UsageRecord {
    pub dimensions: UsageDimensions,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cost: CostBreakdown,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct UsageTotals {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cost_microusd: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct UsageRollup {
    pub by_organization: BTreeMap<String, UsageTotals>,
    pub by_project: BTreeMap<(String, String), UsageTotals>,
    pub by_user: BTreeMap<(String, String), UsageTotals>,
}

impl UsageRollup {
    pub fn from_records(records: &[UsageRecord]) -> Option<Self> {
        let mut result = Self::default();
        for record in records {
            let cost = record.cost.total_microusd()?;
            add(
                result
                    .by_organization
                    .entry(record.dimensions.organization_id.clone())
                    .or_default(),
                record,
                cost,
            )?;
            add(
                result
                    .by_project
                    .entry((
                        record.dimensions.organization_id.clone(),
                        record.dimensions.project_id.clone(),
                    ))
                    .or_default(),
                record,
                cost,
            )?;
            add(
                result
                    .by_user
                    .entry((
                        record.dimensions.organization_id.clone(),
                        record.dimensions.user_id.clone(),
                    ))
                    .or_default(),
                record,
                cost,
            )?;
        }
        Some(result)
    }
}

fn add(total: &mut UsageTotals, record: &UsageRecord, cost: u64) -> Option<()> {
    total.input_tokens = total.input_tokens.checked_add(record.input_tokens)?;
    total.output_tokens = total.output_tokens.checked_add(record.output_tokens)?;
    total.cost_microusd = total.cost_microusd.checked_add(cost)?;
    Some(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cost_detects_overflow() {
        assert_eq!(
            CostBreakdown {
                inference_microusd: u64::MAX,
                tools_microusd: 1,
                ..Default::default()
            }
            .total_microusd(),
            None
        );
    }

    #[test]
    fn rollup_preserves_org_project_user_totals() {
        let records = vec![UsageRecord {
            dimensions: UsageDimensions {
                organization_id: "org".into(),
                project_id: "project".into(),
                environment_id: "prod".into(),
                user_id: "user".into(),
                api_key_id: "key".into(),
                model_id: "vendor/model".into(),
                agent_id: None,
                tool_id: None,
            },
            input_tokens: 10,
            output_tokens: 5,
            cost: CostBreakdown {
                inference_microusd: 100,
                verification_microusd: 20,
                ..Default::default()
            },
        }];
        let rollup = UsageRollup::from_records(&records).unwrap();
        assert_eq!(rollup.by_organization["org"].cost_microusd, 120);
        assert_eq!(
            rollup.by_project[&("org".into(), "project".into())].input_tokens,
            10
        );
        assert_eq!(
            rollup.by_user[&("org".into(), "user".into())].output_tokens,
            5
        );
    }
}
