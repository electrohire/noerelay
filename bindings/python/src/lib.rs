use noerelay_domain::{
    Candidate, CanonicalRequest, Constraints, ContextCompiler, ContextNode, ContractCompiler,
    Ledger, ReceiptVerifier, Router, SignedRunReceipt,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn invalid(error: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(error.to_string())
}

#[pyfunction]
fn compile_contract(request_json: &str) -> PyResult<String> {
    let request: CanonicalRequest = serde_json::from_str(request_json).map_err(invalid)?;
    let contract = ContractCompiler.compile(&request).map_err(invalid)?;
    serde_json::to_string(&contract).map_err(invalid)
}

#[pyfunction]
fn select_route(candidates_json: &str, constraints_json: &str) -> PyResult<String> {
    let candidates: Vec<Candidate> = serde_json::from_str(candidates_json).map_err(invalid)?;
    let constraints: Constraints = serde_json::from_str(constraints_json).map_err(invalid)?;
    serde_json::to_string(&Router.select(&candidates, &constraints)).map_err(invalid)
}

#[pyfunction]
fn compile_context(nodes_json: &str, budget_tokens: u32) -> PyResult<String> {
    let nodes: Vec<ContextNode> = serde_json::from_str(nodes_json).map_err(invalid)?;
    let manifest = ContextCompiler
        .compile(&nodes, budget_tokens)
        .map_err(invalid)?;
    serde_json::to_string(&manifest).map_err(invalid)
}

#[pyfunction]
fn verify_ledger(ledger_json: &str) -> PyResult<bool> {
    let ledger: Ledger = serde_json::from_str(ledger_json).map_err(invalid)?;
    ledger.verify().map_err(invalid)?;
    Ok(true)
}

#[pyfunction]
fn verify_receipt(
    signed_receipt_json: &str,
    trusted_key_id: &str,
    trusted_public_key_base64: &str,
) -> PyResult<bool> {
    let receipt: SignedRunReceipt = serde_json::from_str(signed_receipt_json).map_err(invalid)?;
    ReceiptVerifier::from_public_key_base64(trusted_key_id, trusted_public_key_base64)
        .map_err(invalid)?
        .verify(&receipt)
        .map_err(invalid)?;
    Ok(true)
}

#[pymodule(name = "noerelay_core")]
fn python_module(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(compile_contract, module)?)?;
    module.add_function(wrap_pyfunction!(select_route, module)?)?;
    module.add_function(wrap_pyfunction!(compile_context, module)?)?;
    module.add_function(wrap_pyfunction!(verify_ledger, module)?)?;
    module.add_function(wrap_pyfunction!(verify_receipt, module)?)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_json_is_a_python_value_error() {
        Python::initialize();
        Python::attach(|_| {
            let error = compile_contract("not-json").unwrap_err();
            assert!(error.to_string().contains("expected ident"));
        });
    }
}
