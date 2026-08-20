/// NoeRelay RTK (Rust Token Killer) — native compression module.
///
/// PyO3 bindings exposing fast token estimation and message compression
/// to Python.  Mirrors the algorithms in `reference/gateway/compression.py`.
///
/// Usage from Python:
///     import noerelay_rtk
///     result = noerelay_rtk.compress_messages_rust(messages, "auto", 0.5, 512)

mod dedup;
mod prune;
mod tokenizer;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Python-visible functions
// ---------------------------------------------------------------------------

/// Fast token estimation for a text string.
#[pyfunction]
fn estimate_tokens_rust(text: &str) -> usize {
    tokenizer::estimate_tokens(text)
}

/// Count tokens across a list of message dicts.
#[pyfunction]
fn count_message_tokens_rust(messages: &Bound<'_, PyDict>) -> PyResult<usize> {
    let items: Vec<Bound<'_, PyDict>> = extract_dict_list(messages)?;
    let contents: Vec<String> = items
        .iter()
        .filter_map(|msg| {
            msg.get_item("content")
                .ok()
                .flatten()
                .and_then(|v| v.extract::<String>().ok())
        })
        .collect();
    Ok(tokenizer::count_message_tokens(&contents))
}

/// Deduplicate a message list in Rust — returns list of dicts.
#[pyfunction]
fn dedup_compress_rust(py: Python<'_>, messages: &Bound<'_, PyDict>) -> PyResult<Py<PyDict>> {
    let msgs = extract_dict_list(messages)?;
    let native_msgs: Vec<HashMap<String, String>> = msgs
        .iter()
        .map(|d| dict_to_hashmap(py, d))
        .collect::<PyResult<Vec<_>>>()?;
    let compressed = dedup::dedup_compress(&native_msgs);
    hashmap_list_to_pydict(py, &compressed)
}

/// Prune a message list in Rust — returns list of dicts.
#[pyfunction]
fn prune_compress_rust(py: Python<'_>, messages: &Bound<'_, PyDict>) -> PyResult<Py<PyDict>> {
    let msgs = extract_dict_list(messages)?;
    let native_msgs: Vec<HashMap<String, String>> = msgs
        .iter()
        .map(|d| dict_to_hashmap(py, d))
        .collect::<PyResult<Vec<_>>>()?;
    let compressed = prune::prune_compress(&native_msgs);
    hashmap_list_to_pydict(py, &compressed)
}

/// Auto compress (dedup + prune if needed) in Rust — returns list of dicts.
#[pyfunction]
fn auto_compress_rust(py: Python<'_>, messages: &Bound<'_, PyDict>, _target_ratio: f64) -> PyResult<Py<PyDict>> {
    let msgs = extract_dict_list(messages)?;
    let native_msgs: Vec<HashMap<String, String>> = msgs
        .iter()
        .map(|d| dict_to_hashmap(py, d))
        .collect::<PyResult<Vec<_>>>()?;

    let deduped = dedup::dedup_compress(&native_msgs);
    let pruned = prune::prune_compress(&deduped);

    // Pick the one with fewer tokens
    let deduped_contents: Vec<String> = deduped
        .iter()
        .map(|m| m.get("content").cloned().unwrap_or_default())
        .collect();
    let pruned_contents: Vec<String> = pruned
        .iter()
        .map(|m| m.get("content").cloned().unwrap_or_default())
        .collect();

    let deduped_tokens = tokenizer::count_message_tokens(&deduped_contents);
    let pruned_tokens = tokenizer::count_message_tokens(&pruned_contents);

    let best = if pruned_tokens < deduped_tokens {
        pruned
    } else {
        deduped
    };

    hashmap_list_to_pydict(py, &best)
}

/// Main compression entry point — mirrors `compress_messages` in Python.
///
/// Returns a dict with keys: original_messages, compressed_messages,
/// original_token_count, compressed_token_count, compression_ratio, strategy,
/// duration_ms, tokens_saved, skipped.
#[pyfunction]
fn compress_messages_rust(
    py: Python<'_>,
    messages: &Bound<'_, PyDict>,
    strategy: &str,
    _target_ratio: f64,
    min_tokens: usize,
) -> PyResult<Py<PyDict>> {
    let start = std::time::Instant::now();

    let msgs = extract_dict_list(messages)?;
    let native_msgs: Vec<HashMap<String, String>> = msgs
        .iter()
        .map(|d| dict_to_hashmap(py, d))
        .collect::<PyResult<Vec<_>>>()?;

    let original_contents: Vec<String> = native_msgs
        .iter()
        .map(|m| m.get("content").cloned().unwrap_or_default())
        .collect();
    let original_tokens = tokenizer::count_message_tokens(&original_contents);

    // Check skip conditions
    let result_dict = PyDict::new_bound(py);

    // Store original messages for return
    let original_py = py_list_from_hashmaps(py, &native_msgs)?;

    if original_tokens <= min_tokens {
        let duration_ms = start.elapsed().as_secs_f64() * 1000.0;
        result_dict.set_item("original_messages", &original_py)?;
        result_dict.set_item("compressed_messages", &original_py)?;
        result_dict.set_item("original_token_count", original_tokens)?;
        result_dict.set_item("compressed_token_count", original_tokens)?;
        result_dict.set_item("compression_ratio", 0.0)?;
        result_dict.set_item("strategy", strategy)?;
        result_dict.set_item("duration_ms", duration_ms)?;
        result_dict.set_item("tokens_saved", 0)?;
        result_dict.set_item("skipped", true)?;
        return Ok(result_dict.into());
    }

    // Apply strategy
    let compressed = match strategy {
        "dedup" => dedup::dedup_compress(&native_msgs),
        "prune" => prune::prune_compress(&native_msgs),
        _ => {
            // auto: dedup then prune if needed
            let deduped = dedup::dedup_compress(&native_msgs);
            let pruned = prune::prune_compress(&deduped);
            let deduped_contents: Vec<String> = deduped
                .iter()
                .map(|m| m.get("content").cloned().unwrap_or_default())
                .collect();
            let pruned_contents: Vec<String> = pruned
                .iter()
                .map(|m| m.get("content").cloned().unwrap_or_default())
                .collect();
            let d_tokens = tokenizer::count_message_tokens(&deduped_contents);
            let p_tokens = tokenizer::count_message_tokens(&pruned_contents);
            if p_tokens < d_tokens { pruned } else { deduped }
        }
    };

    let compressed_contents: Vec<String> = compressed
        .iter()
        .map(|m| m.get("content").cloned().unwrap_or_default())
        .collect();
    let compressed_tokens = tokenizer::count_message_tokens(&compressed_contents);
    let tokens_saved = original_tokens.saturating_sub(compressed_tokens);
    let ratio = tokens_saved as f64 / std::cmp::max(original_tokens, 1) as f64;
    let duration_ms = start.elapsed().as_secs_f64() * 1000.0;

    let compressed_py = py_list_from_hashmaps(py, &compressed)?;

    result_dict.set_item("original_messages", &original_py)?;
    result_dict.set_item("compressed_messages", &compressed_py)?;
    result_dict.set_item("original_token_count", original_tokens)?;
    result_dict.set_item("compressed_token_count", compressed_tokens)?;
    result_dict.set_item("compression_ratio", (ratio * 10000.0).round() / 10000.0)?;
    result_dict.set_item("strategy", strategy)?;
    result_dict.set_item("duration_ms", (duration_ms * 1000.0).round() / 1000.0)?;
    result_dict.set_item("tokens_saved", tokens_saved)?;
    result_dict.set_item("skipped", false)?;

    Ok(result_dict.into())
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Extract a list of PyDict references from a PyDict (expects a Python list).
fn extract_dict_list<'py>(container: &Bound<'py, PyDict>) -> PyResult<Vec<Bound<'py, PyDict>>> {
    // Try to get as a list from the "messages" key
    if let Ok(Some(list_obj)) = container.get_item("messages") {
        if let Ok(list) = list_obj.downcast::<PyList>() {
            let mut result = Vec::new();
            for item in list.iter() {
                if let Ok(dict) = item.downcast::<PyDict>() {
                    result.push(dict.clone());
                }
            }
            return Ok(result);
        }
    }
    Err(pyo3::exceptions::PyTypeError::new_err(
        "expected a dict with 'messages' key containing a list of message dicts",
    ))
}

/// Convert a PyDict to a Rust HashMap<String, String>.
fn dict_to_hashmap(_py: Python<'_>, dict: &Bound<'_, PyDict>) -> PyResult<HashMap<String, String>> {
    let mut map = HashMap::new();
    for (key, value) in dict.iter() {
        let k: String = key.extract()?;
        let v: String = value.extract().unwrap_or_else(|_| {
            // For non-string values, use repr()
            value.repr().map(|s| s.to_string()).unwrap_or_default()
        });
        map.insert(k, v);
    }
    Ok(map)
}

/// Convert a slice of HashMaps to a Python dict containing a "messages" list.
fn py_list_from_hashmaps(py: Python<'_>, maps: &[HashMap<String, String>]) -> PyResult<Py<PyDict>> {
    let outer = PyDict::new_bound(py);
    let list = PyList::empty_bound(py);

    for map in maps {
        let d = PyDict::new_bound(py);
        for (k, v) in map {
            d.set_item(k, v)?;
        }
        list.append(d)?;
    }

    outer.set_item("messages", list)?;
    Ok(outer.into())
}

/// Convert a slice of HashMaps to a Python dict containing a "messages" list.
fn hashmap_list_to_pydict(py: Python<'_>, maps: &[HashMap<String, String>]) -> PyResult<Py<PyDict>> {
    py_list_from_hashmaps(py, maps)
}

// ---------------------------------------------------------------------------
// Module definition
// ---------------------------------------------------------------------------

#[pymodule]
fn noerelay_rtk(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(estimate_tokens_rust, m)?)?;
    m.add_function(wrap_pyfunction!(count_message_tokens_rust, m)?)?;
    m.add_function(wrap_pyfunction!(dedup_compress_rust, m)?)?;
    m.add_function(wrap_pyfunction!(prune_compress_rust, m)?)?;
    m.add_function(wrap_pyfunction!(auto_compress_rust, m)?)?;
    m.add_function(wrap_pyfunction!(compress_messages_rust, m)?)?;

    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("__doc__", "NoeRelay RTK — native compression module")?;

    Ok(())
}