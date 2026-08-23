use anyhow::Context;
use noerelay_core::*;
use std::fmt::Debug;
use std::fs;

/// Run golden vector round-trip tests for all canonical types.
pub fn run() -> anyhow::Result<()> {
    let golden_dir = std::path::Path::new("spec/schemas/golden");
    if !golden_dir.exists() {
        anyhow::bail!(
            "Golden vector directory {} does not exist",
            golden_dir.display()
        );
    }

    let mut passed = 0;
    let mut failed = 0;

    // TaskContract
    run_type::<TaskContract>(&mut passed, &mut failed)?;
    // SignedRunReceipt
    run_type::<SignedRunReceipt>(&mut passed, &mut failed)?;
    // Candidate
    run_type::<Candidate>(&mut passed, &mut failed)?;
    // RouteDecision
    run_type::<RouteDecision>(&mut passed, &mut failed)?;
    // ContextManifest
    run_type::<ContextManifest>(&mut passed, &mut failed)?;
    // Claim
    run_type::<Claim>(&mut passed, &mut failed)?;
    // Evidence
    run_type::<Evidence>(&mut passed, &mut failed)?;
    // LedgerEvent
    run_type::<LedgerEvent>(&mut passed, &mut failed)?;
    // CanonicalRequest
    run_type::<CanonicalRequest>(&mut passed, &mut failed)?;
    // RunReceipt
    run_type::<RunReceipt>(&mut passed, &mut failed)?;
    // BudgetAccount
    run_type::<BudgetAccount>(&mut passed, &mut failed)?;
    // UsageRecord
    run_type::<UsageRecord>(&mut passed, &mut failed)?;
    // ToolRevision
    run_type::<ToolRevision>(&mut passed, &mut failed)?;
    // Requirement
    run_type::<Requirement>(&mut passed, &mut failed)?;
    // ModelObservation
    run_type::<ModelObservation>(&mut passed, &mut failed)?;

    eprintln!("\nGolden vector results: {passed} passed, {failed} failed");
    if failed > 0 {
        anyhow::bail!("{failed} golden vector test(s) failed");
    }
    Ok(())
}

/// Create a sample value. Returns a sensible default. The round-trip test
/// serializes -> deserializes -> compares for equality.
fn sample_of<T: Sample>() -> T {
    T::sample()
}

fn run_type<T>(passed: &mut u32, failed: &mut u32) -> anyhow::Result<()>
where
    T: serde::Serialize + serde::de::DeserializeOwned + Debug + PartialEq + Sample,
{
    let type_name = std::any::type_name::<T>();
    let sample = sample_of::<T>();
    let json =
        serde_json::to_string_pretty(&sample).with_context(|| format!("serialize {type_name}"))?;
    let roundtripped: T =
        serde_json::from_str(&json).with_context(|| format!("deserialize {type_name}"))?;
    if sample == roundtripped {
        *passed += 1;
        eprintln!("PASS {type_name} round-trip");
    } else {
        *failed += 1;
        eprintln!("FAIL {type_name} round-trip mismatch");
    }
    // Write golden file
    let short_name = type_name.split("::").last().unwrap_or(type_name);
    let file_name = format!("spec/schemas/golden/{short_name}.golden.json");
    fs::write(&file_name, &json).with_context(|| format!("write golden file {file_name}"))?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Sample trait and implementations
// ---------------------------------------------------------------------------

pub trait Sample {
    fn sample() -> Self;
}

impl Sample for TaskContract {
    fn sample() -> Self {
        TaskContract {
            contract_version: "1.0.0".into(),
            contract_hash: "a".repeat(64),
            request_id: "req-sample".into(),
            outcome: "Build a reliable API endpoint".into(),
            risk: RiskClass::Medium,
            acceptance_criteria: vec!["Tests pass".into(), "Review approved".into()],
            required_capabilities: ["coding".into()].into(),
            allowed_tools: [].into(),
            allowed_agents: [].into(),
            max_cost_microusd: Some(50_000),
            max_latency_ms: Some(30_000),
            requires_independent_verifier: false,
            requires_human_approval: false,
            context_manifest_hash: None,
        }
    }
}

impl Sample for RunReceipt {
    fn sample() -> Self {
        RunReceipt {
            receipt_version: "1.0.0".into(),
            run_id: "run-sample".into(),
            organization_id: "org".into(),
            project_id: "project".into(),
            user_id: "user".into(),
            contract_hash: "a".repeat(64),
            selected_candidate_id: "vendor/model".into(),
            output_sha256: "b".repeat(64),
            actual_cost_microusd: 1_500,
            cost_source: "estimated".into(),
            input_tokens: 500,
            output_tokens: 200,
            release_outcome: ReleaseOutcome::Accepted,
            ledger_head: "c".repeat(64),
            receipt_hash: "d".repeat(64),
        }
    }
}

impl Sample for SignedRunReceipt {
    fn sample() -> Self {
        let receipt = RunReceipt::sample();
        use base64::Engine;
        let pk_bytes = [0xABu8; 32];
        let sig_bytes = [0xCDu8; 64];
        SignedRunReceipt {
            receipt,
            algorithm: "Ed25519".into(),
            signing_key_id: "key-sample-1".into(),
            public_key_base64: base64::engine::general_purpose::STANDARD.encode(pk_bytes),
            signature_base64: base64::engine::general_purpose::STANDARD.encode(sig_bytes),
        }
    }
}

impl Sample for Candidate {
    fn sample() -> Self {
        Candidate {
            candidate_id: "candidate-1".into(),
            openrouter_model_id: "anthropic/claude-sonnet-4".into(),
            provider: "anthropic".into(),
            available: true,
            capabilities: ["coding".into(), "text".into()].into(),
            maximum_data_class: DataClass::Confidential,
            cost: CostBreakdown::sample(),
            latency_p95_ms: 250,
            acceptance_lcb_ppm: 975_000,
            supports_independent_verification: true,
        }
    }
}

impl Sample for RouteDecision {
    fn sample() -> Self {
        RouteDecision {
            selected_candidate_id: Some("candidate-1".into()),
            selected_openrouter_model_id: Some("anthropic/claude-sonnet-4".into()),
            expected_total_cost_microusd: Some(1_500),
            rejections: vec![CandidateRejection {
                candidate_id: "candidate-2".into(),
                reasons: vec![RejectionReason::CostCap],
            }],
        }
    }
}

impl Sample for ContextManifest {
    fn sample() -> Self {
        ContextManifest {
            budget_tokens: 10_000,
            used_tokens: 150,
            included: vec![ContextNode {
                node_id: "req-1".into(),
                kind: NodeKind::Requirement,
                content: "Must support HTTPS".into(),
                source_handle: "ledger:req-1".into(),
                estimated_tokens: 50,
                salience_ppm: 900_000,
                sequence: 1,
                explicitly_protected: false,
            }],
            omitted_node_ids: vec!["chat-1".into()],
            manifest_hash: "e".repeat(64),
        }
    }
}

impl Sample for Claim {
    fn sample() -> Self {
        Claim {
            claim_id: "claim-1".into(),
            kind: ClaimKind::Fact,
            statement: "All tests pass for v1.0.0".into(),
            state: EpistemicState::Supported,
            supporting_evidence: vec!["evidence-1".into()],
            refuting_evidence: vec![],
        }
    }
}

impl Sample for Evidence {
    fn sample() -> Self {
        Evidence {
            evidence_id: "evidence-1".into(),
            test_id: "T-1".into(),
            source_revision: "5a24249a9098a6c468da45d27a449fab380863b5".into(),
            artifact_hash: "f".repeat(64),
            status: EvidenceStatus::ObservedPass,
        }
    }
}

impl Sample for LedgerEvent {
    fn sample() -> Self {
        LedgerEvent {
            sequence: 1,
            occurred_at_unix_ms: 1_724_000_000_000,
            organization_id: "org".into(),
            project_id: "project".into(),
            run_id: "run-sample".into(),
            kind: LedgerEventKind::RequestAccepted,
            payload: serde_json::json!({"request_id": "req-sample"}),
            previous_hash: "0000000000000000000000000000000000000000000000000000000000000000"
                .into(),
            event_hash: "a".repeat(64),
        }
    }
}

impl Sample for CanonicalRequest {
    fn sample() -> Self {
        CanonicalRequest {
            request_id: "req-sample".into(),
            scope: IdentityScope {
                organization_id: "org".into(),
                project_id: "project".into(),
                environment_id: "prod".into(),
                user_id: "user@example.invalid".into(),
                session_id: "session-1".into(),
            },
            messages: vec![Message {
                role: MessageRole::User,
                content: "Build a reliable API".into(),
                name: None,
                tool_call_id: None,
            }],
            risk: RiskClass::Medium,
            data_class: DataClass::Internal,
            acceptance_criteria: vec![],
            required_capabilities: vec!["coding".into()],
            allowed_tools: vec![],
            allowed_agents: vec![],
            metadata: std::collections::BTreeMap::new(),
            max_cost_microusd: None,
            max_latency_ms: None,
        }
    }
}

impl Sample for BudgetAccount {
    fn sample() -> Self {
        BudgetAccount::new(100_000)
    }
}

impl Sample for UsageRecord {
    fn sample() -> Self {
        UsageRecord {
            dimensions: UsageDimensions {
                organization_id: "org".into(),
                project_id: "project".into(),
                environment_id: "prod".into(),
                user_id: "user".into(),
                api_key_id: "key-1".into(),
                model_id: "vendor/model".into(),
                agent_id: None,
                tool_id: None,
            },
            input_tokens: 500,
            output_tokens: 200,
            cost: CostBreakdown::sample(),
        }
    }
}

impl Sample for ToolRevision {
    fn sample() -> Self {
        ToolRevision {
            tool_id: "deploy".into(),
            revision: "1".into(),
            allowed_organizations: [].into(),
            allowed_projects: [].into(),
            maximum_data_class: DataClass::Confidential,
            side_effecting: true,
            allowed_egress_hosts: ["api.example.invalid".into()].into(),
            maximum_input_bytes: 10_000,
            maximum_output_bytes: 50_000,
        }
    }
}

impl Sample for Requirement {
    fn sample() -> Self {
        Requirement {
            requirement_id: "NR-1".into(),
            architecture_refs: ["core".into()].into(),
            outcome: "System accepts runs via the canonical API".into(),
            must: true,
        }
    }
}

impl Sample for ModelObservation {
    fn sample() -> Self {
        ModelObservation {
            cohort: "coding".into(),
            candidate_id: "candidate-1".into(),
            accepted: true,
            cost_microusd: 1_500,
            latency_ms: 250,
            observed_at_unix_ms: 1_724_000_000_000,
        }
    }
}

impl Sample for CostBreakdown {
    fn sample() -> Self {
        CostBreakdown {
            inference_microusd: 1_000,
            tools_microusd: 200,
            verification_microusd: 100,
            expected_retry_microusd: 100,
            expected_fallback_microusd: 50,
            infrastructure_microusd: 25,
            expected_human_review_microusd: 25,
        }
    }
}
