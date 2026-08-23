use anyhow::Context;
use std::fs;
use std::path::Path;

/// Generate JSON Schema files for all canonical Rust types.
pub fn generate_json() -> anyhow::Result<()> {
    let out_dir = Path::new("spec/schemas/generated");
    fs::create_dir_all(out_dir).context("create generated/ directory")?;

    let mut manifest_entries: Vec<(String, String)> = Vec::new();

    // Generate schemas for each canonical type using schemars::schema_for!
    // The types are organized by module.

    // types.rs
    gen_and_write::<noerelay_core::RiskClass>(out_dir, "risk_class", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::DataClass>(out_dir, "data_class", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::IdentityScope>(
        out_dir,
        "identity_scope",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::MessageRole>(out_dir, "message_role", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::Message>(out_dir, "message", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::CanonicalRequest>(
        out_dir,
        "canonical_request",
        &mut manifest_entries,
    )?;

    // contract.rs
    gen_and_write::<noerelay_core::TaskContract>(out_dir, "task_contract", &mut manifest_entries)?;

    // routing.rs
    gen_and_write::<noerelay_core::Candidate>(out_dir, "candidate", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::Constraints>(out_dir, "constraints", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::RejectionReason>(
        out_dir,
        "rejection_reason",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::CandidateRejection>(
        out_dir,
        "candidate_rejection",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::RouteDecision>(
        out_dir,
        "route_decision",
        &mut manifest_entries,
    )?;

    // runtime.rs
    gen_and_write::<noerelay_core::PreparedRun>(out_dir, "prepared_run", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::RunReceipt>(out_dir, "run_receipt", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::Completion>(out_dir, "completion", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::UsageMeasurement>(
        out_dir,
        "usage_measurement",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::GovernanceSnapshot>(
        out_dir,
        "governance_snapshot",
        &mut manifest_entries,
    )?;

    // receipt.rs
    gen_and_write::<noerelay_core::SignedRunReceipt>(
        out_dir,
        "signed_run_receipt",
        &mut manifest_entries,
    )?;

    // budget.rs
    gen_and_write::<noerelay_core::BudgetReservation>(
        out_dir,
        "budget_reservation",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::BudgetAccount>(
        out_dir,
        "budget_account",
        &mut manifest_entries,
    )?;

    // context.rs
    gen_and_write::<noerelay_core::NodeKind>(out_dir, "node_kind", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::ContextNode>(out_dir, "context_node", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::ContextManifest>(
        out_dir,
        "context_manifest",
        &mut manifest_entries,
    )?;

    // epistemic.rs
    gen_and_write::<noerelay_core::EpistemicState>(
        out_dir,
        "epistemic_state",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::EvidencePolarity>(
        out_dir,
        "evidence_polarity",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::ClaimKind>(out_dir, "claim_kind", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::Claim>(out_dir, "claim", &mut manifest_entries)?;

    // ledger.rs
    gen_and_write::<noerelay_core::LedgerEventKind>(
        out_dir,
        "ledger_event_kind",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::LedgerEvent>(out_dir, "ledger_event", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::Ledger>(out_dir, "ledger", &mut manifest_entries)?;

    // verification.rs
    gen_and_write::<noerelay_core::CheckKind>(out_dir, "check_kind", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::VerificationCheck>(
        out_dir,
        "verification_check",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::CheckStatus>(out_dir, "check_status", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::CheckResult>(out_dir, "check_result", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::ReleaseOutcome>(
        out_dir,
        "release_outcome",
        &mut manifest_entries,
    )?;

    // usage.rs
    gen_and_write::<noerelay_core::CostBreakdown>(
        out_dir,
        "cost_breakdown",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::UsageDimensions>(
        out_dir,
        "usage_dimensions",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::UsageRecord>(out_dir, "usage_record", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::UsageTotals>(out_dir, "usage_totals", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::UsageRollup>(out_dir, "usage_rollup", &mut manifest_entries)?;

    // tools.rs
    gen_and_write::<noerelay_core::ToolRevision>(out_dir, "tool_revision", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::ToolProposal>(out_dir, "tool_proposal", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::ToolContext>(out_dir, "tool_context", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::ToolDecision>(out_dir, "tool_decision", &mut manifest_entries)?;

    // traceability.rs
    gen_and_write::<noerelay_core::Requirement>(out_dir, "requirement", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::TestCase>(out_dir, "test_case", &mut manifest_entries)?;
    gen_and_write::<noerelay_core::EvidenceStatus>(
        out_dir,
        "evidence_status",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::Evidence>(out_dir, "evidence", &mut manifest_entries)?;

    // recommendation.rs
    gen_and_write::<noerelay_core::ModelObservation>(
        out_dir,
        "model_observation",
        &mut manifest_entries,
    )?;
    gen_and_write::<noerelay_core::Recommendation>(
        out_dir,
        "recommendation",
        &mut manifest_entries,
    )?;

    // Write the manifest
    let manifest = serde_json::json!({
        "schema_version": "1.0.0",
        "types": manifest_entries.iter().map(|(name, file)| {
            serde_json::json!({
                "type_name": name,
                "file": file,
            })
        }).collect::<Vec<_>>()
    });
    fs::write(
        out_dir.join("manifest.json"),
        serde_json::to_string_pretty(&manifest)?,
    )
    .context("write manifest.json")?;

    eprintln!(
        "Generated {} JSON Schema files in {}",
        manifest_entries.len(),
        out_dir.display()
    );
    Ok(())
}

fn gen_and_write<T: schemars::JsonSchema>(
    out_dir: &Path,
    file_stem: &str,
    manifest: &mut Vec<(String, String)>,
) -> anyhow::Result<()> {
    let schema = schemars::schema_for!(T);
    let json = serde_json::to_string_pretty(&schema)?;
    let file_name = format!("{file_stem}.schema.json");
    let file_path = out_dir.join(&file_name);
    fs::write(&file_path, json).context(format!("write {file_name}"))?;
    manifest.push((std::any::type_name::<T>().to_owned(), file_name));
    Ok(())
}

pub fn generate_openapi() -> anyhow::Result<()> {
    anyhow::bail!("OpenAPI component generation is not yet implemented");
}

pub fn diff() -> anyhow::Result<()> {
    anyhow::bail!(
        "Schema diff is not yet implemented. Run 'xtask schema json' first to generate schemas."
    );
}
