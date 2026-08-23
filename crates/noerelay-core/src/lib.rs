//! Trusted, deterministic release-authority core for NoeRelay.
//!
//! Framework, network, persistence, and language-binding concerns live outside
//! this crate. The types and pure decisions here are the canonical semantics.

pub mod artifacts;
pub mod budget;
pub mod context;
pub mod contract;
pub mod epistemic;
pub mod evidence;
pub mod execution;
pub mod governance;
pub mod iam;
pub mod ledger;
pub mod receipt;
pub mod recommendation;
pub mod registry;
pub mod routing;
pub mod runtime;
pub mod tools;
pub mod traceability;
pub mod types;
pub mod usage;
pub mod verification;
pub mod wire;

pub use artifacts::*;
pub use budget::{BudgetAccount, BudgetError, BudgetReservation};
pub use context::{ContextCompiler, ContextError, ContextManifest, ContextNode, NodeKind};
pub use contract::{ContractCompiler, ContractError, TaskContract};
pub use epistemic::{Claim, ClaimKind, EpistemicState, EvidencePolarity};
pub use evidence::{EnvelopeStatus, EvidenceEnvelope};
pub use execution::*;
pub use governance::*;
pub use iam::*;
pub use ledger::{Ledger, LedgerError, LedgerEvent, LedgerEventKind};
pub use receipt::{ReceiptSignatureError, ReceiptSigner, ReceiptVerifier, SignedRunReceipt};
pub use recommendation::{ModelObservation, Recommendation, Recommender};
pub use registry::{
    AgentRevision, DataPolicy, HealthStatus, Modality, ModelRevision, PriceSnapshot,
    ProvenanceInfo, ProviderRevision, RateLimitInfo, RegistryEntityType, RegistryError,
    RegistryLifecycle, ToolRevision as RegistryToolRevision,
};
pub use routing::{
    Candidate, CandidateRejection, Constraints, RejectionReason, RouteDecision, Router,
};
pub use runtime::{
    Completion, GovernanceRuntime, GovernanceSnapshot, PreparedRun, RunReceipt, RuntimeError,
    UsageMeasurement,
};
pub use tools::{ToolAuthorization, ToolContext, ToolDecision, ToolProposal, ToolRevision};
pub use traceability::{Evidence, EvidenceStatus, Requirement, TestCase, TraceError, TraceGraph};
pub use types::{CanonicalRequest, DataClass, IdentityScope, Message, MessageRole, RiskClass};
pub use usage::{CostBreakdown, UsageDimensions, UsageRecord, UsageRollup, UsageTotals};
pub use verification::{
    CheckKind, CheckResult, CheckStatus, ReleaseOutcome, VerificationCheck, VerificationDag,
    VerificationError,
};
// The established governance request remains `CanonicalRequest` at crate root.
// The API transport IR is exported under an unambiguous root alias and retains
// its canonical name within the `wire` module.
pub use wire::{
    ApiError, ApiErrorBody, CanonicalRequest as WireCanonicalRequest, CanonicalResponse,
    ChatCompletionsConverter, CompatibilityProfile, FieldValidator, ProfileSupport,
    ResponsesConverter,
};
