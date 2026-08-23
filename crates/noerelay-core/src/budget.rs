use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BudgetReservation {
    pub reservation_id: String,
    pub amount_microusd: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BudgetAccount {
    pub limit_microusd: u64,
    pub spent_microusd: u64,
    reservations: BTreeMap<String, u64>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum BudgetError {
    #[error("reservation already exists")]
    DuplicateReservation,
    #[error("reservation does not exist")]
    UnknownReservation,
    #[error("budget is insufficient")]
    InsufficientBudget,
    #[error("budget arithmetic overflow")]
    ArithmeticOverflow,
    #[error("actual cost exceeds the reserved amount")]
    ReconciliationExceedsReservation,
}

impl BudgetAccount {
    pub fn new(limit_microusd: u64) -> Self {
        Self {
            limit_microusd,
            spent_microusd: 0,
            reservations: BTreeMap::new(),
        }
    }

    pub fn reserved_microusd(&self) -> Option<u64> {
        self.reservations
            .values()
            .copied()
            .try_fold(0_u64, u64::checked_add)
    }

    pub fn available_microusd(&self) -> Option<u64> {
        self.limit_microusd
            .checked_sub(self.spent_microusd)?
            .checked_sub(self.reserved_microusd()?)
    }

    pub fn reserve(
        &mut self,
        reservation_id: impl Into<String>,
        amount_microusd: u64,
    ) -> Result<BudgetReservation, BudgetError> {
        let reservation_id = reservation_id.into();
        if self.reservations.contains_key(&reservation_id) {
            return Err(BudgetError::DuplicateReservation);
        }
        if amount_microusd
            > self
                .available_microusd()
                .ok_or(BudgetError::ArithmeticOverflow)?
        {
            return Err(BudgetError::InsufficientBudget);
        }
        self.reservations
            .insert(reservation_id.clone(), amount_microusd);
        Ok(BudgetReservation {
            reservation_id,
            amount_microusd,
        })
    }

    pub fn reconcile(
        &mut self,
        reservation_id: &str,
        actual_microusd: u64,
    ) -> Result<(), BudgetError> {
        let reserved = *self
            .reservations
            .get(reservation_id)
            .ok_or(BudgetError::UnknownReservation)?;
        if actual_microusd > reserved {
            return Err(BudgetError::ReconciliationExceedsReservation);
        }
        let spent = self
            .spent_microusd
            .checked_add(actual_microusd)
            .ok_or(BudgetError::ArithmeticOverflow)?;
        self.reservations.remove(reservation_id);
        self.spent_microusd = spent;
        Ok(())
    }

    pub fn release(&mut self, reservation_id: &str) -> Result<(), BudgetError> {
        self.reservations
            .remove(reservation_id)
            .map(|_| ())
            .ok_or(BudgetError::UnknownReservation)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reservations_prevent_overspend_before_execution() {
        let mut account = BudgetAccount::new(100);
        account.reserve("one", 80).unwrap();
        assert_eq!(
            account.reserve("two", 21),
            Err(BudgetError::InsufficientBudget)
        );
        assert_eq!(account.available_microusd(), Some(20));
    }

    #[test]
    fn failed_reconciliation_is_atomic() {
        let mut account = BudgetAccount::new(100);
        account.reserve("one", 80).unwrap();
        assert_eq!(
            account.reconcile("one", 81),
            Err(BudgetError::ReconciliationExceedsReservation)
        );
        assert_eq!(account.reserved_microusd(), Some(80));
        assert_eq!(account.spent_microusd, 0);
    }

    #[test]
    fn reconciliation_returns_unused_capacity() {
        let mut account = BudgetAccount::new(100);
        account.reserve("one", 80).unwrap();
        account.reconcile("one", 30).unwrap();
        assert_eq!(account.spent_microusd, 30);
        assert_eq!(account.available_microusd(), Some(70));
    }
}
