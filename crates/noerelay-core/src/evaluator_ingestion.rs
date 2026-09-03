//! Evaluator ingestion contract — outcome-to-action mapping per mission §8.
//!
//! Maps Spec Kit evaluator outcomes to NoeRelay actions and provides
//! lifecycle hook integration for Spec Kit phases.

use crate::evaluator_result::EvaluatorOutcome;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// NoeRelay action derived from an evaluator outcome.
///
/// Per mission §8 table:
///
/// | Outcome          | NoeRelay action                                      |
/// |------------------|------------------------------------------------------|
/// | `pass`           | Continue if evidence gates are independently satisfied. |
/// | `warn`           | Continue with findings preserved; optionally deepen verification. |
/// | `iterate`        | Create a bounded repair/revision task.                |
/// | `clarify`        | Pause and request clarification.                      |
/// | `gather_evidence`| Create a bounded evidence-collection task.            |
/// | `block`          | Stop and record the blocker.                          |
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum NoerelayAction {
    /// Continue the workflow; evidence gates must still be independently satisfied.
    Continue,
    /// Continue with findings preserved; optionally deepen verification.
    ContinueWithWarnings,
    /// Create a bounded repair/revision task.
    CreateRepairTask,
    /// Pause and request clarification from the user.
    RequestClarification,
    /// Create a bounded evidence-collection task.
    GatherEvidence,
    /// Stop and record the blocker; do not proceed.
    Block,
}

/// Map an evaluator outcome to the corresponding NoeRelay action.
pub fn map_outcome_to_action(outcome: EvaluatorOutcome) -> NoerelayAction {
    match outcome {
        EvaluatorOutcome::Pass => NoerelayAction::Continue,
        EvaluatorOutcome::Warn => NoerelayAction::ContinueWithWarnings,
        EvaluatorOutcome::Iterate => NoerelayAction::CreateRepairTask,
        EvaluatorOutcome::Clarify => NoerelayAction::RequestClarification,
        EvaluatorOutcome::GatherEvidence => NoerelayAction::GatherEvidence,
        EvaluatorOutcome::Block => NoerelayAction::Block,
    }
}

/// Spec Kit lifecycle hook identifiers.
///
/// These correspond to the phases where evaluators may be invoked.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum SpecKitHook {
    AfterSpecify,
    AfterPlan,
    AfterTasks,
    AfterImplement,
}

/// Result of invoking a Spec Kit lifecycle hook.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct HookResult {
    /// The hook that was invoked.
    pub hook: SpecKitHook,
    /// The evaluator outcome.
    pub outcome: EvaluatorOutcome,
    /// The derived NoeRelay action.
    pub action: NoerelayAction,
    /// Whether the workflow may continue.
    pub may_continue: bool,
    /// Human-readable summary of the hook result.
    pub summary: String,
}

impl HookResult {
    pub fn new(hook: SpecKitHook, outcome: EvaluatorOutcome, summary: impl Into<String>) -> Self {
        let action = map_outcome_to_action(outcome);
        let may_continue = matches!(
            action,
            NoerelayAction::Continue | NoerelayAction::ContinueWithWarnings
        );
        Self {
            hook,
            outcome,
            action,
            may_continue,
            summary: summary.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pass_maps_to_continue() {
        assert_eq!(
            map_outcome_to_action(EvaluatorOutcome::Pass),
            NoerelayAction::Continue
        );
    }

    #[test]
    fn warn_maps_to_continue_with_warnings() {
        assert_eq!(
            map_outcome_to_action(EvaluatorOutcome::Warn),
            NoerelayAction::ContinueWithWarnings
        );
    }

    #[test]
    fn iterate_maps_to_create_repair_task() {
        assert_eq!(
            map_outcome_to_action(EvaluatorOutcome::Iterate),
            NoerelayAction::CreateRepairTask
        );
    }

    #[test]
    fn clarify_maps_to_request_clarification() {
        assert_eq!(
            map_outcome_to_action(EvaluatorOutcome::Clarify),
            NoerelayAction::RequestClarification
        );
    }

    #[test]
    fn gather_evidence_maps_to_gather_evidence() {
        assert_eq!(
            map_outcome_to_action(EvaluatorOutcome::GatherEvidence),
            NoerelayAction::GatherEvidence
        );
    }

    #[test]
    fn block_maps_to_block() {
        assert_eq!(
            map_outcome_to_action(EvaluatorOutcome::Block),
            NoerelayAction::Block
        );
    }

    #[test]
    fn pass_hook_allows_continue() {
        let result = HookResult::new(
            SpecKitHook::AfterImplement,
            EvaluatorOutcome::Pass,
            "all tests passed",
        );
        assert!(result.may_continue);
        assert_eq!(result.action, NoerelayAction::Continue);
    }

    #[test]
    fn block_hook_prevents_continue() {
        let result = HookResult::new(
            SpecKitHook::AfterImplement,
            EvaluatorOutcome::Block,
            "security violation detected",
        );
        assert!(!result.may_continue);
        assert_eq!(result.action, NoerelayAction::Block);
    }

    #[test]
    fn all_outcomes_have_mappings() {
        // Ensure every variant is covered
        let outcomes = [
            EvaluatorOutcome::Pass,
            EvaluatorOutcome::Warn,
            EvaluatorOutcome::Iterate,
            EvaluatorOutcome::Clarify,
            EvaluatorOutcome::GatherEvidence,
            EvaluatorOutcome::Block,
        ];
        for outcome in outcomes {
            let action = map_outcome_to_action(outcome);
            // Every outcome must produce a valid action
            assert!(!format!("{:?}", action).is_empty());
        }
    }
}
