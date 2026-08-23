use noerelay_core::{
    Candidate, CanonicalRequest, CheckResult, CheckStatus, Constraints, CostBreakdown, DataClass,
    GovernanceRuntime, IdentityScope, Message, ReceiptSigner, RiskClass, UsageMeasurement,
};
use noerelay_store::PostgresAuthorityStore;
use std::collections::{BTreeMap, BTreeSet};

#[tokio::test]
async fn committed_authority_reloads_with_receipt_and_valid_chain() {
    let Ok(database_url) = std::env::var("NOERELAY_TEST_DATABASE_URL") else {
        eprintln!("NOERELAY_TEST_DATABASE_URL unset; PostgreSQL integration test skipped");
        return;
    };
    let store = PostgresAuthorityStore::connect(&database_url, 2)
        .await
        .unwrap();
    let suffix = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let organization_id = format!("postgres-test-org-{suffix}");
    let project_id = format!("postgres-test-project-{suffix}");
    let run_id = format!("postgres-roundtrip-run-{suffix}");
    let mut runtime = GovernanceRuntime::new(1_000);
    let request = CanonicalRequest {
        request_id: run_id,
        scope: IdentityScope {
            organization_id,
            project_id,
            environment_id: "test".into(),
            user_id: "postgres-test-user".into(),
            session_id: "postgres-test-session".into(),
        },
        messages: vec![Message {
            role: noerelay_core::types::MessageRole::User,
            content: "exercise durable authority".into(),
            name: None,
            tool_call_id: None,
        }],
        risk: RiskClass::Low,
        data_class: DataClass::Internal,
        acceptance_criteria: vec![],
        required_capabilities: vec!["text".into()],
        allowed_tools: vec![],
        allowed_agents: vec![],
        metadata: BTreeMap::new(),
        max_cost_microusd: Some(500),
        max_latency_ms: Some(1_000),
    };
    let candidate = Candidate {
        candidate_id: "durable-model".into(),
        openrouter_model_id: "vendor/durable-model".into(),
        provider: "vendor".into(),
        available: true,
        capabilities: BTreeSet::from(["text".into()]),
        maximum_data_class: DataClass::Confidential,
        cost: CostBreakdown {
            inference_microusd: 100,
            ..Default::default()
        },
        latency_p95_ms: 10,
        acceptance_lcb_ppm: 999_999,
        supports_independent_verification: true,
    };
    let constraints = Constraints {
        required_capabilities: BTreeSet::from(["text".into()]),
        data_class: DataClass::Internal,
        allowed_providers: BTreeSet::new(),
        max_total_cost_microusd: Some(500),
        max_latency_ms: Some(1_000),
        min_acceptance_lcb_ppm: 900_000,
        require_independent_verification: false,
    };
    runtime
        .prepare(&request, &[candidate], &constraints, 1)
        .unwrap();
    let completion = runtime
        .complete(
            &request.request_id,
            b"durable result",
            &UsageMeasurement {
                cost_microusd: 90,
                cost_source: "estimated".into(),
                input_tokens: 10,
                output_tokens: 5,
            },
            "durable-model",
            &[CheckResult {
                check_id: "response_schema".into(),
                status: CheckStatus::Passed,
                observed_evidence_id: Some("observed-response".into()),
                verifier_family: None,
            }],
            2,
        )
        .unwrap();
    let signer = ReceiptSigner::from_seed("test-key", [9; 32]).unwrap();
    let verifier = signer.verifying_key();
    let receipt = signer.sign(completion.receipt.unwrap()).unwrap();
    let version = store
        .save(
            &request.scope.organization_id,
            &request.scope.project_id,
            0,
            &runtime.snapshot(),
            Some(&receipt),
        )
        .await
        .unwrap();
    assert_eq!(version, 1);

    let mut conflicting_receipt = receipt.clone();
    conflicting_receipt.receipt.receipt_hash = "conflicting-receipt-hash".into();
    let conflict = store
        .save(
            &request.scope.organization_id,
            &request.scope.project_id,
            1,
            &runtime.snapshot(),
            Some(&conflicting_receipt),
        )
        .await;
    assert!(matches!(
        conflict,
        Err(noerelay_store::StoreError::VersionConflict)
    ));

    let loaded = store
        .load(&request.scope.organization_id, &request.scope.project_id)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(loaded.storage_version, 1);
    assert_eq!(loaded.snapshot.ledger.head(), runtime.ledger().head());
    assert_eq!(loaded.snapshot.budget.spent_microusd, 90);
    let stored_receipt = store
        .receipt(&request.scope.organization_id, &request.request_id)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        stored_receipt.receipt.receipt_hash,
        receipt.receipt.receipt_hash
    );
    verifier.verify(&stored_receipt).unwrap();
    let rollups = store
        .cost_rollups(
            &request.scope.organization_id,
            Some(&request.scope.project_id),
        )
        .await
        .unwrap();
    assert_eq!(rollups.len(), 1);
    assert_eq!(rollups[0].requests, 1);
    assert_eq!(rollups[0].input_tokens, 10);
    assert_eq!(rollups[0].output_tokens, 5);
    assert_eq!(rollups[0].cost_microusd, 90);
}
