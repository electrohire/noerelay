use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ModelObservation {
    pub cohort: String,
    pub candidate_id: String,
    pub accepted: bool,
    pub cost_microusd: u64,
    pub latency_ms: u64,
    pub observed_at_unix_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Recommendation {
    pub cohort: String,
    pub candidate_id: Option<String>,
    pub sample_count: u64,
    pub acceptance_lcb_ppm: u32,
    pub mean_cost_microusd: Option<u64>,
    pub mean_latency_ms: Option<u64>,
    pub fresh_through_unix_ms: Option<u64>,
    pub advisory_only: bool,
    pub reason: String,
}

#[derive(Debug, Clone, Copy)]
pub struct Recommender {
    pub minimum_samples: u64,
}

impl Recommender {
    pub fn recommend(&self, cohort: &str, observations: &[ModelObservation]) -> Recommendation {
        #[derive(Default)]
        struct Aggregate {
            count: u64,
            accepted: u64,
            cost: u128,
            latency: u128,
            fresh: u64,
        }

        let mut candidates: BTreeMap<&str, Aggregate> = BTreeMap::new();
        for observation in observations.iter().filter(|item| item.cohort == cohort) {
            let aggregate = candidates.entry(&observation.candidate_id).or_default();
            aggregate.count = aggregate.count.saturating_add(1);
            aggregate.accepted = aggregate
                .accepted
                .saturating_add(u64::from(observation.accepted));
            aggregate.cost = aggregate
                .cost
                .saturating_add(u128::from(observation.cost_microusd));
            aggregate.latency = aggregate
                .latency
                .saturating_add(u128::from(observation.latency_ms));
            aggregate.fresh = aggregate.fresh.max(observation.observed_at_unix_ms);
        }

        let mut ranked: Vec<(&str, &Aggregate, u32)> = candidates
            .iter()
            .filter(|(_, aggregate)| aggregate.count >= self.minimum_samples)
            .map(|(id, aggregate)| {
                (
                    *id,
                    aggregate,
                    wilson_lcb_ppm(aggregate.accepted, aggregate.count),
                )
            })
            .collect();
        ranked.sort_by_key(|(id, aggregate, lcb)| {
            (
                std::cmp::Reverse(*lcb),
                aggregate.cost / u128::from(aggregate.count),
                aggregate.latency / u128::from(aggregate.count),
                *id,
            )
        });

        let Some((candidate_id, aggregate, lcb)) = ranked.first() else {
            return Recommendation {
                cohort: cohort.into(),
                candidate_id: None,
                sample_count: candidates.values().map(|item| item.count).sum(),
                acceptance_lcb_ppm: 0,
                mean_cost_microusd: None,
                mean_latency_ms: None,
                fresh_through_unix_ms: candidates.values().map(|item| item.fresh).max(),
                advisory_only: true,
                reason: "insufficient scoped observations".into(),
            };
        };

        Recommendation {
            cohort: cohort.into(),
            candidate_id: Some((*candidate_id).into()),
            sample_count: aggregate.count,
            acceptance_lcb_ppm: *lcb,
            mean_cost_microusd: u64::try_from(aggregate.cost / u128::from(aggregate.count)).ok(),
            mean_latency_ms: u64::try_from(aggregate.latency / u128::from(aggregate.count)).ok(),
            fresh_through_unix_ms: Some(aggregate.fresh),
            advisory_only: true,
            reason: "highest Wilson lower bound; cost and latency break ties".into(),
        }
    }
}

fn wilson_lcb_ppm(successes: u64, total: u64) -> u32 {
    if total == 0 {
        return 0;
    }
    let n = total as f64;
    let p = successes as f64 / n;
    let z = 1.959_963_984_540_054_f64;
    let denominator = 1.0 + z * z / n;
    let center = p + z * z / (2.0 * n);
    let margin = z * ((p * (1.0 - p) + z * z / (4.0 * n)) / n).sqrt();
    (((center - margin) / denominator).clamp(0.0, 1.0) * 1_000_000.0).round() as u32
}

#[cfg(test)]
mod tests {
    use super::*;

    fn observations(candidate: &str, accepted: usize, failed: usize) -> Vec<ModelObservation> {
        (0..accepted + failed)
            .map(|index| ModelObservation {
                cohort: "coding".into(),
                candidate_id: candidate.into(),
                accepted: index < accepted,
                cost_microusd: if candidate == "cheap" { 10 } else { 100 },
                latency_ms: 100,
                observed_at_unix_ms: index as u64,
            })
            .collect()
    }

    #[test]
    fn insufficient_data_abstains() {
        let result = Recommender {
            minimum_samples: 10,
        }
        .recommend("coding", &observations("one", 1, 0));
        assert_eq!(result.candidate_id, None);
        assert!(result.advisory_only);
    }

    #[test]
    fn stronger_acceptance_wins_before_cost() {
        let mut values = observations("cheap", 8, 2);
        values.extend(observations("reliable", 10, 0));
        let result = Recommender {
            minimum_samples: 10,
        }
        .recommend("coding", &values);
        assert_eq!(result.candidate_id.as_deref(), Some("reliable"));
        assert!(result.acceptance_lcb_ppm > 0);
    }

    #[test]
    fn cohort_data_does_not_cross_contaminate() {
        let mut values = observations("coding-model", 10, 0);
        values.push(ModelObservation {
            cohort: "legal".into(),
            candidate_id: "legal-model".into(),
            accepted: true,
            cost_microusd: 1,
            latency_ms: 1,
            observed_at_unix_ms: 100,
        });
        let result = Recommender {
            minimum_samples: 10,
        }
        .recommend("coding", &values);
        assert_eq!(result.candidate_id.as_deref(), Some("coding-model"));
    }
}
