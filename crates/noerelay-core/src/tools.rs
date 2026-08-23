use crate::types::{DataClass, IdentityScope, RiskClass};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ToolRevision {
    pub tool_id: String,
    pub revision: String,
    pub allowed_organizations: BTreeSet<String>,
    pub allowed_projects: BTreeSet<String>,
    pub maximum_data_class: DataClass,
    pub side_effecting: bool,
    pub allowed_egress_hosts: BTreeSet<String>,
    pub maximum_input_bytes: u64,
    pub maximum_output_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ToolProposal {
    pub tool_id: String,
    pub input_bytes: u64,
    pub requested_egress_host: Option<String>,
    pub idempotency_key: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ToolContext {
    pub scope: IdentityScope,
    pub risk: RiskClass,
    pub granted_tools: BTreeSet<String>,
    pub human_approval_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ToolDecision {
    Allow,
    UnknownTool,
    NotGranted,
    OrganizationDenied,
    ProjectDenied,
    DataPolicy,
    InputTooLarge,
    EgressDenied,
    IdempotencyRequired,
    ApprovalRequired,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct ToolAuthorization;

impl ToolAuthorization {
    pub fn decide(
        &self,
        revision: Option<&ToolRevision>,
        proposal: &ToolProposal,
        context: &ToolContext,
        data_class: DataClass,
    ) -> ToolDecision {
        let Some(revision) = revision else {
            return ToolDecision::UnknownTool;
        };
        if revision.tool_id != proposal.tool_id
            || !context.granted_tools.contains(&proposal.tool_id)
        {
            return ToolDecision::NotGranted;
        }
        if !revision.allowed_organizations.is_empty()
            && !revision
                .allowed_organizations
                .contains(&context.scope.organization_id)
        {
            return ToolDecision::OrganizationDenied;
        }
        if !revision.allowed_projects.is_empty()
            && !revision
                .allowed_projects
                .contains(&context.scope.project_id)
        {
            return ToolDecision::ProjectDenied;
        }
        if revision.maximum_data_class < data_class {
            return ToolDecision::DataPolicy;
        }
        if proposal.input_bytes > revision.maximum_input_bytes {
            return ToolDecision::InputTooLarge;
        }
        if proposal.requested_egress_host.as_ref().is_some_and(|host| {
            !revision
                .allowed_egress_hosts
                .contains(&host.to_ascii_lowercase())
        }) {
            return ToolDecision::EgressDenied;
        }
        if revision.side_effecting
            && proposal
                .idempotency_key
                .as_ref()
                .is_none_or(|key| key.trim().is_empty())
        {
            return ToolDecision::IdempotencyRequired;
        }
        if revision.side_effecting
            && matches!(context.risk, RiskClass::High | RiskClass::Critical)
            && context.human_approval_id.is_none()
        {
            return ToolDecision::ApprovalRequired;
        }
        ToolDecision::Allow
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scope() -> IdentityScope {
        IdentityScope {
            organization_id: "org".into(),
            project_id: "project".into(),
            environment_id: "prod".into(),
            user_id: "user".into(),
            session_id: "session".into(),
        }
    }

    fn revision() -> ToolRevision {
        ToolRevision {
            tool_id: "deploy".into(),
            revision: "1".into(),
            allowed_organizations: BTreeSet::from(["org".into()]),
            allowed_projects: BTreeSet::from(["project".into()]),
            maximum_data_class: DataClass::Confidential,
            side_effecting: true,
            allowed_egress_hosts: BTreeSet::from(["api.example.invalid".into()]),
            maximum_input_bytes: 1_000,
            maximum_output_bytes: 2_000,
        }
    }

    fn proposal() -> ToolProposal {
        ToolProposal {
            tool_id: "deploy".into(),
            input_bytes: 100,
            requested_egress_host: Some("api.example.invalid".into()),
            idempotency_key: Some("idem-1".into()),
        }
    }

    fn context() -> ToolContext {
        ToolContext {
            scope: scope(),
            risk: RiskClass::High,
            granted_tools: BTreeSet::from(["deploy".into()]),
            human_approval_id: Some("approval-1".into()),
        }
    }

    #[test]
    fn fully_authorized_proposal_is_allowed() {
        assert_eq!(
            ToolAuthorization.decide(
                Some(&revision()),
                &proposal(),
                &context(),
                DataClass::Internal
            ),
            ToolDecision::Allow
        );
    }

    #[test]
    fn advertised_tool_without_grant_is_denied() {
        let mut denied = context();
        denied.granted_tools.clear();
        assert_eq!(
            ToolAuthorization.decide(Some(&revision()), &proposal(), &denied, DataClass::Internal),
            ToolDecision::NotGranted
        );
    }

    #[test]
    fn side_effect_requires_idempotency_before_approval() {
        let mut value = proposal();
        value.idempotency_key = None;
        let mut denied = context();
        denied.human_approval_id = None;
        assert_eq!(
            ToolAuthorization.decide(Some(&revision()), &value, &denied, DataClass::Internal),
            ToolDecision::IdempotencyRequired
        );
    }

    #[test]
    fn unlisted_egress_is_denied() {
        let mut value = proposal();
        value.requested_egress_host = Some("attacker.invalid".into());
        assert_eq!(
            ToolAuthorization.decide(Some(&revision()), &value, &context(), DataClass::Internal),
            ToolDecision::EgressDenied
        );
    }
}
