use crate::{
    BudgetAccount, BudgetError, Candidate, CanonicalRequest, CheckKind, CheckResult, Constraints,
    ContractCompiler, ContractError, Ledger, LedgerError, LedgerEventKind, ReleaseOutcome,
    RouteDecision, Router, TaskContract, VerificationCheck, VerificationDag, VerificationError,
};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PreparedRun {
    pub run_id: String,
    pub scope: crate::IdentityScope,
    pub contract: TaskContract,
    pub route: RouteDecision,
    pub reserved_cost_microusd: u64,
    pub verification_checks: Vec<VerificationCheck>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RunReceipt {
    pub receipt_version: String,
    pub run_id: String,
    pub organization_id: String,
    pub project_id: String,
    pub user_id: String,
    pub contract_hash: String,
    pub selected_candidate_id: String,
    pub output_sha256: String,
    pub actual_cost_microusd: u64,
    pub cost_source: String,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub release_outcome: ReleaseOutcome,
    pub ledger_head: String,
    pub receipt_hash: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Completion {
    pub outcome: ReleaseOutcome,
    pub receipt: Option<RunReceipt>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct UsageMeasurement {
    pub cost_microusd: u64,
    pub cost_source: String,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

#[derive(Debug, Error)]
pub enum RuntimeError {
    #[error("contract rejected: {0}")]
    Contract(#[from] ContractError),
    #[error("no admissible route")]
    NoAdmissibleRoute,
    #[error("run already exists")]
    DuplicateRun,
    #[error("run does not exist")]
    UnknownRun,
    #[error("budget rejected: {0}")]
    Budget(#[from] BudgetError),
    #[error("ledger rejected: {0}")]
    Ledger(#[from] LedgerError),
    #[error("verification rejected: {0}")]
    Verification(#[from] VerificationError),
    #[error("internal serialization failed: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("cost source must be estimated, provider_reported, or billed")]
    InvalidCostSource,
    #[error("invalid authority snapshot: {0}")]
    InvalidSnapshot(String),
}

#[derive(Debug, Clone)]
struct ActiveRun {
    prepared: PreparedRun,
    verification: VerificationDag,
}

/// Transactional in-process authority state. Durable repositories can implement
/// the same transitions, but callers never mutate budgets or the ledger directly.
#[derive(Debug, Clone)]
pub struct GovernanceRuntime {
    budget: BudgetAccount,
    ledger: Ledger,
    active: BTreeMap<String, ActiveRun>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct GovernanceSnapshot {
    pub snapshot_version: String,
    pub budget: BudgetAccount,
    pub ledger: Ledger,
    pub active_runs: Vec<PreparedRun>,
}

impl GovernanceRuntime {
    pub fn new(budget_limit_microusd: u64) -> Self {
        Self {
            budget: BudgetAccount::new(budget_limit_microusd),
            ledger: Ledger::default(),
            active: BTreeMap::new(),
        }
    }

    pub fn budget(&self) -> &BudgetAccount {
        &self.budget
    }

    pub fn ledger(&self) -> &Ledger {
        &self.ledger
    }

    pub fn snapshot(&self) -> GovernanceSnapshot {
        GovernanceSnapshot {
            snapshot_version: "1.0.0".into(),
            budget: self.budget.clone(),
            ledger: self.ledger.clone(),
            active_runs: self
                .active
                .values()
                .map(|active| active.prepared.clone())
                .collect(),
        }
    }

    pub fn from_snapshot(snapshot: GovernanceSnapshot) -> Result<Self, RuntimeError> {
        if snapshot.snapshot_version != "1.0.0" {
            return Err(RuntimeError::InvalidSnapshot(format!(
                "unsupported version {}",
                snapshot.snapshot_version
            )));
        }
        if snapshot.budget.available_microusd().is_none() {
            return Err(RuntimeError::InvalidSnapshot(
                "budget totals exceed the configured limit or overflow".into(),
            ));
        }
        snapshot.ledger.verify()?;
        let mut active = BTreeMap::new();
        for prepared in snapshot.active_runs {
            let verification = VerificationDag::new(prepared.verification_checks.clone())?;
            if active
                .insert(
                    prepared.run_id.clone(),
                    ActiveRun {
                        prepared,
                        verification,
                    },
                )
                .is_some()
            {
                return Err(RuntimeError::DuplicateRun);
            }
        }
        Ok(Self {
            budget: snapshot.budget,
            ledger: snapshot.ledger,
            active,
        })
    }

    pub fn prepare(
        &mut self,
        request: &CanonicalRequest,
        candidates: &[Candidate],
        constraints: &Constraints,
        occurred_at_unix_ms: u64,
    ) -> Result<PreparedRun, RuntimeError> {
        if self.active.contains_key(&request.request_id) {
            return Err(RuntimeError::DuplicateRun);
        }
        let contract = ContractCompiler.compile(request)?;
        let route = Router.select(candidates, constraints);
        let reserved_cost_microusd = route
            .expected_total_cost_microusd
            .ok_or(RuntimeError::NoAdmissibleRoute)?;
        let checks = verification_plan(&contract);
        let verification = VerificationDag::new(checks.clone())?;

        // Stage every authoritative mutation on clones. No partial reservation
        // or hash-chain append can escape if any later transition fails.
        let mut budget = self.budget.clone();
        let mut ledger = self.ledger.clone();
        budget.reserve(&request.request_id, reserved_cost_microusd)?;
        ledger.append(
            occurred_at_unix_ms,
            &request.scope.organization_id,
            &request.scope.project_id,
            &request.request_id,
            LedgerEventKind::RequestAccepted,
            json!({"request_id": request.request_id, "user_id": request.scope.user_id}),
        )?;
        ledger.append(
            occurred_at_unix_ms,
            &request.scope.organization_id,
            &request.scope.project_id,
            &request.request_id,
            LedgerEventKind::ContractCompiled,
            json!({"contract_hash": contract.contract_hash}),
        )?;
        ledger.append(
            occurred_at_unix_ms,
            &request.scope.organization_id,
            &request.scope.project_id,
            &request.request_id,
            LedgerEventKind::RouteSelected,
            serde_json::to_value(&route)?,
        )?;
        let prepared = PreparedRun {
            run_id: request.request_id.clone(),
            scope: request.scope.clone(),
            contract,
            route,
            reserved_cost_microusd,
            verification_checks: checks,
        };
        self.budget = budget;
        self.ledger = ledger;
        self.active.insert(
            request.request_id.clone(),
            ActiveRun {
                prepared: prepared.clone(),
                verification,
            },
        );
        Ok(prepared)
    }

    pub fn complete(
        &mut self,
        run_id: &str,
        output: &[u8],
        usage: &UsageMeasurement,
        worker_family: &str,
        results: &[CheckResult],
        occurred_at_unix_ms: u64,
    ) -> Result<Completion, RuntimeError> {
        if !matches!(
            usage.cost_source.as_str(),
            "estimated" | "provider_reported" | "billed"
        ) {
            return Err(RuntimeError::InvalidCostSource);
        }
        let active = self
            .active
            .get(run_id)
            .cloned()
            .ok_or(RuntimeError::UnknownRun)?;
        let outcome =
            active
                .verification
                .evaluate(active.prepared.contract.risk, worker_family, results)?;
        let output_sha256 = hex::encode(Sha256::digest(output));
        let mut budget = self.budget.clone();
        let mut ledger = self.ledger.clone();
        budget.reconcile(run_id, usage.cost_microusd)?;
        for result in results {
            ledger.append(
                occurred_at_unix_ms,
                &active.prepared.scope.organization_id,
                &active.prepared.scope.project_id,
                run_id,
                LedgerEventKind::VerificationObserved,
                serde_json::to_value(result)?,
            )?;
        }
        ledger.append(
            occurred_at_unix_ms,
            &active.prepared.scope.organization_id,
            &active.prepared.scope.project_id,
            run_id,
            LedgerEventKind::CostReconciled,
            json!({
                "actual_cost_microusd": usage.cost_microusd,
                "cost_source": usage.cost_source,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens
            }),
        )?;
        ledger.append(
            occurred_at_unix_ms,
            &active.prepared.scope.organization_id,
            &active.prepared.scope.project_id,
            run_id,
            if outcome == ReleaseOutcome::Accepted {
                LedgerEventKind::RunReleased
            } else {
                LedgerEventKind::RunRejected
            },
            json!({"outcome": outcome, "output_sha256": output_sha256}),
        )?;
        ledger.verify()?;

        let receipt = if outcome == ReleaseOutcome::Accepted {
            Some(build_receipt(
                &active.prepared,
                output_sha256,
                usage.cost_microusd,
                &usage.cost_source,
                usage.input_tokens,
                usage.output_tokens,
                ledger.head(),
            )?)
        } else {
            None
        };
        self.budget = budget;
        self.ledger = ledger;
        self.active.remove(run_id);
        Ok(Completion { outcome, receipt })
    }

    pub fn abort(
        &mut self,
        run_id: &str,
        reason_code: &str,
        occurred_at_unix_ms: u64,
    ) -> Result<(), RuntimeError> {
        let active = self
            .active
            .get(run_id)
            .cloned()
            .ok_or(RuntimeError::UnknownRun)?;
        let mut budget = self.budget.clone();
        let mut ledger = self.ledger.clone();
        budget.release(run_id)?;
        ledger.append(
            occurred_at_unix_ms,
            &active.prepared.scope.organization_id,
            &active.prepared.scope.project_id,
            run_id,
            LedgerEventKind::RunRejected,
            json!({"reason_code": reason_code}),
        )?;
        ledger.verify()?;
        self.budget = budget;
        self.ledger = ledger;
        self.active.remove(run_id);
        Ok(())
    }
}

fn verification_plan(contract: &TaskContract) -> Vec<VerificationCheck> {
    let mut checks = vec![VerificationCheck {
        check_id: "response_schema".into(),
        kind: CheckKind::Schema,
        depends_on: BTreeSet::new(),
        verifier_family: None,
    }];
    let mut prior = BTreeSet::from(["response_schema".to_owned()]);
    for (index, _) in contract.acceptance_criteria.iter().enumerate() {
        let check_id = format!("acceptance_{index}");
        checks.push(VerificationCheck {
            check_id: check_id.clone(),
            kind: CheckKind::DeterministicAcceptance,
            depends_on: BTreeSet::from(["response_schema".to_owned()]),
            verifier_family: None,
        });
        prior.insert(check_id);
    }
    if contract.requires_independent_verifier {
        checks.push(VerificationCheck {
            check_id: "independent_review".into(),
            kind: CheckKind::IndependentReview,
            depends_on: prior,
            verifier_family: None,
        });
    }
    if contract.requires_human_approval {
        checks.push(VerificationCheck {
            check_id: "human_approval".into(),
            kind: CheckKind::HumanApproval,
            depends_on: BTreeSet::from(["independent_review".to_owned()]),
            verifier_family: None,
        });
    }
    checks
}

fn build_receipt(
    prepared: &PreparedRun,
    output_sha256: String,
    actual_cost_microusd: u64,
    cost_source: &str,
    input_tokens: u64,
    output_tokens: u64,
    ledger_head: &str,
) -> Result<RunReceipt, serde_json::Error> {
    #[derive(Serialize)]
    struct Material<'a> {
        receipt_version: &'a str,
        run_id: &'a str,
        organization_id: &'a str,
        project_id: &'a str,
        user_id: &'a str,
        contract_hash: &'a str,
        selected_candidate_id: &'a str,
        output_sha256: &'a str,
        actual_cost_microusd: u64,
        cost_source: &'a str,
        input_tokens: u64,
        output_tokens: u64,
        release_outcome: ReleaseOutcome,
        ledger_head: &'a str,
    }
    let selected_candidate_id = prepared
        .route
        .selected_candidate_id
        .as_deref()
        .expect("prepared route is selected");
    let material = Material {
        receipt_version: "1.0.0",
        run_id: &prepared.run_id,
        organization_id: &prepared.scope.organization_id,
        project_id: &prepared.scope.project_id,
        user_id: &prepared.scope.user_id,
        contract_hash: &prepared.contract.contract_hash,
        selected_candidate_id,
        output_sha256: &output_sha256,
        actual_cost_microusd,
        cost_source,
        input_tokens,
        output_tokens,
        release_outcome: ReleaseOutcome::Accepted,
        ledger_head,
    };
    let receipt_hash = hex::encode(Sha256::digest(serde_json::to_vec(&material)?));
    Ok(RunReceipt {
        receipt_version: material.receipt_version.into(),
        run_id: material.run_id.into(),
        organization_id: material.organization_id.into(),
        project_id: material.project_id.into(),
        user_id: material.user_id.into(),
        contract_hash: material.contract_hash.into(),
        selected_candidate_id: material.selected_candidate_id.into(),
        output_sha256: output_sha256.clone(),
        actual_cost_microusd,
        cost_source: cost_source.into(),
        input_tokens,
        output_tokens,
        release_outcome: material.release_outcome,
        ledger_head: ledger_head.into(),
        receipt_hash,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{CostBreakdown, DataClass, IdentityScope, Message, RiskClass};

    fn request(risk: RiskClass) -> CanonicalRequest {
        CanonicalRequest {
            request_id: "run-1".into(),
            scope: IdentityScope {
                organization_id: "org".into(),
                project_id: "project".into(),
                environment_id: "test".into(),
                user_id: "user".into(),
                session_id: "session".into(),
            },
            messages: vec![Message {
                role: crate::types::MessageRole::User,
                content: "do work".into(),
                name: None,
                tool_call_id: None,
            }],
            risk,
            data_class: DataClass::Internal,
            acceptance_criteria: if risk >= RiskClass::High {
                vec!["tests pass".into()]
            } else {
                vec![]
            },
            required_capabilities: vec!["text".into()],
            allowed_tools: vec![],
            allowed_agents: vec![],
            metadata: BTreeMap::new(),
            max_cost_microusd: None,
            max_latency_ms: None,
        }
    }

    fn candidate(cost: u64) -> Candidate {
        Candidate {
            candidate_id: "model".into(),
            openrouter_model_id: "vendor/model".into(),
            provider: "vendor".into(),
            available: true,
            capabilities: BTreeSet::from(["text".into()]),
            maximum_data_class: DataClass::Confidential,
            cost: CostBreakdown {
                inference_microusd: cost,
                ..Default::default()
            },
            latency_p95_ms: 10,
            acceptance_lcb_ppm: 999_999,
            supports_independent_verification: true,
        }
    }

    fn constraints(risk: RiskClass) -> Constraints {
        Constraints {
            required_capabilities: BTreeSet::from(["text".into()]),
            data_class: DataClass::Internal,
            allowed_providers: BTreeSet::new(),
            max_total_cost_microusd: None,
            max_latency_ms: None,
            min_acceptance_lcb_ppm: 900_000,
            require_independent_verification: risk >= RiskClass::High,
        }
    }

    fn passed(id: &str, family: Option<&str>) -> CheckResult {
        CheckResult {
            check_id: id.into(),
            status: crate::CheckStatus::Passed,
            observed_evidence_id: Some(format!("evidence-{id}")),
            verifier_family: family.map(str::to_owned),
            evidence_kind: Some("observed".into()),
            uncertainty: Some("none".into()),
            recommended_action: Some("none".into()),
            finding_severity: Some("info".into()),
            finding_kind: Some("other".into()),
            description: Some(format!("Check '{id}' passed.")),
            rationale: None,
        }
    }

    #[test]
    fn accepted_run_atomically_reserves_reconciles_ledgers_and_receipts() {
        let mut runtime = GovernanceRuntime::new(100);
        runtime
            .prepare(
                &request(RiskClass::Low),
                &[candidate(80)],
                &constraints(RiskClass::Low),
                1,
            )
            .unwrap();
        assert_eq!(runtime.budget().reserved_microusd(), Some(80));
        let completed = runtime
            .complete(
                "run-1",
                b"answer",
                &UsageMeasurement {
                    cost_microusd: 30,
                    cost_source: "estimated".into(),
                    input_tokens: 10,
                    output_tokens: 5,
                },
                "worker",
                &[passed("response_schema", None)],
                2,
            )
            .unwrap();
        assert_eq!(completed.outcome, ReleaseOutcome::Accepted);
        assert!(completed.receipt.is_some());
        assert_eq!(runtime.budget().spent_microusd, 30);
        assert_eq!(runtime.budget().reserved_microusd(), Some(0));
        assert_eq!(runtime.ledger().verify(), Ok(()));
    }

    #[test]
    fn failed_prepare_does_not_mutate_budget_or_ledger() {
        let mut runtime = GovernanceRuntime::new(10);
        let error = runtime
            .prepare(
                &request(RiskClass::Low),
                &[candidate(11)],
                &constraints(RiskClass::Low),
                1,
            )
            .unwrap_err();
        assert!(matches!(
            error,
            RuntimeError::Budget(BudgetError::InsufficientBudget)
        ));
        assert_eq!(runtime.budget().reserved_microusd(), Some(0));
        assert!(runtime.ledger().events().is_empty());
    }

    #[test]
    fn high_risk_cannot_self_verify() {
        let mut runtime = GovernanceRuntime::new(100);
        runtime
            .prepare(
                &request(RiskClass::High),
                &[candidate(80)],
                &constraints(RiskClass::High),
                1,
            )
            .unwrap();
        let result = runtime.complete(
            "run-1",
            b"answer",
            &UsageMeasurement {
                cost_microusd: 30,
                cost_source: "estimated".into(),
                input_tokens: 10,
                output_tokens: 5,
            },
            "same-family",
            &[
                passed("response_schema", None),
                passed("acceptance_0", None),
                passed("independent_review", Some("same-family")),
            ],
            2,
        );
        assert!(matches!(
            result,
            Err(RuntimeError::Verification(
                VerificationError::NonIndependentVerifier(_)
            ))
        ));
        assert_eq!(runtime.budget().reserved_microusd(), Some(80));
        assert_eq!(runtime.ledger().events().len(), 3);
    }

    #[test]
    fn provider_abort_releases_reservation_and_records_rejection() {
        let mut runtime = GovernanceRuntime::new(100);
        runtime
            .prepare(
                &request(RiskClass::Low),
                &[candidate(80)],
                &constraints(RiskClass::Low),
                1,
            )
            .unwrap();
        runtime.abort("run-1", "provider_failed", 2).unwrap();
        assert_eq!(runtime.budget().available_microusd(), Some(100));
        assert_eq!(
            runtime.ledger().events().last().unwrap().kind,
            LedgerEventKind::RunRejected
        );
    }

    #[test]
    fn snapshot_round_trip_preserves_active_reservations_and_chain() {
        let mut runtime = GovernanceRuntime::new(100);
        runtime
            .prepare(
                &request(RiskClass::Low),
                &[candidate(80)],
                &constraints(RiskClass::Low),
                1,
            )
            .unwrap();
        let restored = GovernanceRuntime::from_snapshot(runtime.snapshot()).unwrap();
        assert_eq!(restored.budget().reserved_microusd(), Some(80));
        assert_eq!(restored.ledger().events().len(), 3);
    }

    #[test]
    fn snapshot_rejects_unknown_versions_and_invalid_budgets() {
        let runtime = GovernanceRuntime::new(100);
        let mut unknown = runtime.snapshot();
        unknown.snapshot_version = "2.0.0".into();
        assert!(matches!(
            GovernanceRuntime::from_snapshot(unknown),
            Err(RuntimeError::InvalidSnapshot(_))
        ));

        let mut invalid_budget = runtime.snapshot();
        invalid_budget.budget.spent_microusd = 101;
        assert!(matches!(
            GovernanceRuntime::from_snapshot(invalid_budget),
            Err(RuntimeError::InvalidSnapshot(_))
        ));
    }
}
