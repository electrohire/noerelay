use crate::RunReceipt;
use base64::{Engine as _, engine::general_purpose::STANDARD};
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::fmt;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SignedRunReceipt {
    pub receipt: RunReceipt,
    pub algorithm: String,
    pub signing_key_id: String,
    pub public_key_base64: String,
    pub signature_base64: String,
}

#[derive(Clone)]
pub struct ReceiptSigner {
    key_id: String,
    key: SigningKey,
}

impl fmt::Debug for ReceiptSigner {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ReceiptSigner")
            .field("key_id", &self.key_id)
            .field(
                "public_key",
                &STANDARD.encode(self.key.verifying_key().to_bytes()),
            )
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ReceiptSignatureError {
    #[error("signing key ID must be between 1 and 128 safe characters")]
    InvalidKeyId,
    #[error("receipt serialization failed")]
    Serialization,
    #[error("receipt signing algorithm is unsupported")]
    Algorithm,
    #[error("receipt signing key ID is not trusted")]
    KeyId,
    #[error("receipt public key does not match the trusted key")]
    PublicKey,
    #[error("receipt signature encoding is invalid")]
    Encoding,
    #[error("receipt signature is invalid")]
    Signature,
}

impl ReceiptSigner {
    pub fn from_seed(
        key_id: impl Into<String>,
        seed: [u8; 32],
    ) -> Result<Self, ReceiptSignatureError> {
        let key_id = key_id.into();
        if key_id.is_empty()
            || key_id.len() > 128
            || !key_id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._:@-".contains(&byte))
        {
            return Err(ReceiptSignatureError::InvalidKeyId);
        }
        Ok(Self {
            key_id,
            key: SigningKey::from_bytes(&seed),
        })
    }

    pub fn sign(&self, receipt: RunReceipt) -> Result<SignedRunReceipt, ReceiptSignatureError> {
        let material =
            serde_json::to_vec(&receipt).map_err(|_| ReceiptSignatureError::Serialization)?;
        let signature = self.key.sign(&material);
        Ok(SignedRunReceipt {
            receipt,
            algorithm: "Ed25519".into(),
            signing_key_id: self.key_id.clone(),
            public_key_base64: STANDARD.encode(self.key.verifying_key().to_bytes()),
            signature_base64: STANDARD.encode(signature.to_bytes()),
        })
    }

    pub fn verifying_key(&self) -> ReceiptVerifier {
        ReceiptVerifier {
            key_id: self.key_id.clone(),
            key: self.key.verifying_key(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ReceiptVerifier {
    key_id: String,
    key: VerifyingKey,
}

impl ReceiptVerifier {
    pub fn from_public_key_base64(
        key_id: impl Into<String>,
        public_key_base64: &str,
    ) -> Result<Self, ReceiptSignatureError> {
        let key_id = key_id.into();
        if key_id.is_empty() || key_id.len() > 128 {
            return Err(ReceiptSignatureError::InvalidKeyId);
        }
        let bytes = STANDARD
            .decode(public_key_base64)
            .map_err(|_| ReceiptSignatureError::Encoding)?;
        let bytes: [u8; 32] = bytes
            .try_into()
            .map_err(|_| ReceiptSignatureError::Encoding)?;
        let key = VerifyingKey::from_bytes(&bytes).map_err(|_| ReceiptSignatureError::Encoding)?;
        Ok(Self { key_id, key })
    }

    pub fn verify(&self, signed: &SignedRunReceipt) -> Result<(), ReceiptSignatureError> {
        if signed.algorithm != "Ed25519" {
            return Err(ReceiptSignatureError::Algorithm);
        }
        if signed.signing_key_id != self.key_id {
            return Err(ReceiptSignatureError::KeyId);
        }
        if signed.public_key_base64 != STANDARD.encode(self.key.to_bytes()) {
            return Err(ReceiptSignatureError::PublicKey);
        }
        let bytes = STANDARD
            .decode(&signed.signature_base64)
            .map_err(|_| ReceiptSignatureError::Encoding)?;
        let signature =
            Signature::from_slice(&bytes).map_err(|_| ReceiptSignatureError::Encoding)?;
        let material = serde_json::to_vec(&signed.receipt)
            .map_err(|_| ReceiptSignatureError::Serialization)?;
        self.key
            .verify(&material, &signature)
            .map_err(|_| ReceiptSignatureError::Signature)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ReleaseOutcome, RunReceipt};

    fn receipt() -> RunReceipt {
        RunReceipt {
            receipt_version: "1.0.0".into(),
            run_id: "run".into(),
            organization_id: "org".into(),
            project_id: "project".into(),
            user_id: "user".into(),
            contract_hash: "a".repeat(64),
            selected_candidate_id: "model".into(),
            output_sha256: "b".repeat(64),
            actual_cost_microusd: 12,
            cost_source: "estimated".into(),
            input_tokens: 10,
            output_tokens: 5,
            release_outcome: ReleaseOutcome::Accepted,
            ledger_head: "c".repeat(64),
            receipt_hash: "d".repeat(64),
        }
    }

    #[test]
    fn trusted_key_verifies_and_tampering_fails() {
        let signer = ReceiptSigner::from_seed("key-1", [7; 32]).unwrap();
        let verifier = signer.verifying_key();
        let mut signed = signer.sign(receipt()).unwrap();
        assert_eq!(verifier.verify(&signed), Ok(()));
        signed.receipt.actual_cost_microusd += 1;
        assert_eq!(
            verifier.verify(&signed),
            Err(ReceiptSignatureError::Signature)
        );
    }

    #[test]
    fn replacement_key_is_not_trusted() {
        let trusted = ReceiptSigner::from_seed("key-1", [7; 32]).unwrap();
        let attacker = ReceiptSigner::from_seed("key-1", [8; 32]).unwrap();
        let signed = attacker.sign(receipt()).unwrap();
        assert_eq!(
            trusted.verifying_key().verify(&signed),
            Err(ReceiptSignatureError::PublicKey)
        );
    }
}
