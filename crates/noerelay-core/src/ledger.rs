use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use thiserror::Error;

const GENESIS_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum LedgerEventKind {
    RequestAccepted,
    ContractCompiled,
    RouteSelected,
    ToolAuthorized,
    AttemptCompleted,
    VerificationObserved,
    ClaimTransitioned,
    CostReconciled,
    RunReleased,
    RunRejected,
    AdministrativeAction,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct LedgerEvent {
    pub sequence: u64,
    pub occurred_at_unix_ms: u64,
    pub organization_id: String,
    pub project_id: String,
    pub run_id: String,
    pub kind: LedgerEventKind,
    pub payload: Value,
    pub previous_hash: String,
    pub event_hash: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Ledger {
    events: Vec<LedgerEvent>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum LedgerError {
    #[error("ledger sequence overflow")]
    SequenceOverflow,
    #[error("floating-point values are prohibited in ledger payloads")]
    FloatingPointPayload,
    #[error("invalid previous hash at sequence {0}")]
    PreviousHash(u64),
    #[error("invalid event hash at sequence {0}")]
    EventHash(u64),
    #[error("invalid sequence at position {position}: observed {observed}")]
    Sequence { position: usize, observed: u64 },
}

impl Ledger {
    pub fn events(&self) -> &[LedgerEvent] {
        &self.events
    }

    pub fn head(&self) -> &str {
        self.events
            .last()
            .map(|event| event.event_hash.as_str())
            .unwrap_or(GENESIS_HASH)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn append(
        &mut self,
        occurred_at_unix_ms: u64,
        organization_id: impl Into<String>,
        project_id: impl Into<String>,
        run_id: impl Into<String>,
        kind: LedgerEventKind,
        payload: Value,
    ) -> Result<&LedgerEvent, LedgerError> {
        if contains_float(&payload) {
            return Err(LedgerError::FloatingPointPayload);
        }
        let sequence = u64::try_from(self.events.len())
            .ok()
            .and_then(|value| value.checked_add(1))
            .ok_or(LedgerError::SequenceOverflow)?;
        let previous_hash = self.head().to_owned();
        let organization_id = organization_id.into();
        let project_id = project_id.into();
        let run_id = run_id.into();
        let event_hash = event_hash(
            sequence,
            occurred_at_unix_ms,
            &organization_id,
            &project_id,
            &run_id,
            kind,
            &payload,
            &previous_hash,
        );
        self.events.push(LedgerEvent {
            sequence,
            occurred_at_unix_ms,
            organization_id,
            project_id,
            run_id,
            kind,
            payload,
            previous_hash,
            event_hash,
        });
        Ok(self.events.last().expect("event was appended"))
    }

    pub fn verify(&self) -> Result<(), LedgerError> {
        let mut previous_hash = GENESIS_HASH;
        for (position, event) in self.events.iter().enumerate() {
            let expected_sequence = u64::try_from(position)
                .ok()
                .and_then(|value| value.checked_add(1))
                .ok_or(LedgerError::SequenceOverflow)?;
            if event.sequence != expected_sequence {
                return Err(LedgerError::Sequence {
                    position,
                    observed: event.sequence,
                });
            }
            if event.previous_hash != previous_hash {
                return Err(LedgerError::PreviousHash(event.sequence));
            }
            let expected_hash = event_hash(
                event.sequence,
                event.occurred_at_unix_ms,
                &event.organization_id,
                &event.project_id,
                &event.run_id,
                event.kind,
                &event.payload,
                &event.previous_hash,
            );
            if event.event_hash != expected_hash {
                return Err(LedgerError::EventHash(event.sequence));
            }
            previous_hash = &event.event_hash;
        }
        Ok(())
    }
}

#[allow(clippy::too_many_arguments)]
fn event_hash(
    sequence: u64,
    occurred_at_unix_ms: u64,
    organization_id: &str,
    project_id: &str,
    run_id: &str,
    kind: LedgerEventKind,
    payload: &Value,
    previous_hash: &str,
) -> String {
    #[derive(Serialize)]
    struct Material<'a> {
        sequence: u64,
        occurred_at_unix_ms: u64,
        organization_id: &'a str,
        project_id: &'a str,
        run_id: &'a str,
        kind: LedgerEventKind,
        payload: &'a Value,
        previous_hash: &'a str,
    }
    let bytes = serde_json::to_vec(&Material {
        sequence,
        occurred_at_unix_ms,
        organization_id,
        project_id,
        run_id,
        kind,
        payload,
        previous_hash,
    })
    .expect("known ledger material is serializable");
    hex::encode(Sha256::digest(bytes))
}

fn contains_float(value: &Value) -> bool {
    match value {
        Value::Number(number) => number.is_f64(),
        Value::Array(items) => items.iter().any(contains_float),
        Value::Object(map) => map.values().any(contains_float),
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn ledger() -> Ledger {
        let mut ledger = Ledger::default();
        ledger
            .append(
                1,
                "org",
                "project",
                "run",
                LedgerEventKind::RequestAccepted,
                json!({"request":"abc"}),
            )
            .unwrap();
        ledger
            .append(
                2,
                "org",
                "project",
                "run",
                LedgerEventKind::ContractCompiled,
                json!({"contract":"def"}),
            )
            .unwrap();
        ledger
    }

    #[test]
    fn valid_chain_verifies() {
        assert_eq!(ledger().verify(), Ok(()));
    }

    #[test]
    fn changed_payload_is_detected() {
        let mut value = ledger();
        value.events[0].payload = json!({"request":"tampered"});
        assert_eq!(value.verify(), Err(LedgerError::EventHash(1)));
    }

    #[test]
    fn deletion_is_detected() {
        let mut value = ledger();
        value.events.remove(0);
        assert!(matches!(value.verify(), Err(LedgerError::Sequence { .. })));
    }

    #[test]
    fn reordering_is_detected() {
        let mut value = ledger();
        value.events.swap(0, 1);
        assert!(matches!(value.verify(), Err(LedgerError::Sequence { .. })));
    }

    #[test]
    fn floating_payload_is_rejected() {
        let error = Ledger::default()
            .append(
                1,
                "org",
                "project",
                "run",
                LedgerEventKind::CostReconciled,
                json!({"cost": 1.2}),
            )
            .unwrap_err();
        assert_eq!(error, LedgerError::FloatingPointPayload);
    }
}
