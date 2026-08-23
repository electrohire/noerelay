use anyhow::{Context, Result};
use noerelay_core::{EnvelopeStatus, EvidenceEnvelope};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::SystemTime;

/// Captured environment information for evidence provenance.
#[derive(Debug, Clone)]
pub struct EnvironmentCapture {
    /// Full commit SHA from `git rev-parse HEAD`.
    pub revision: String,
    /// Output of `rustc --version`.
    #[allow(dead_code)]
    pub rustc_version: String,
    /// Output of `cargo --version`.
    #[allow(dead_code)]
    pub cargo_version: String,
    /// Named deployment profile identifier.
    #[allow(dead_code)]
    pub profile: String,
}

/// Compute the SHA-256 hash of a file and return it as a hex string.
pub fn hash_file(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)
        .with_context(|| format!("failed to open file for hashing: {}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 8192];
    loop {
        let n = file
            .read(&mut buffer)
            .with_context(|| format!("failed to read file for hashing: {}", path.display()))?;
        if n == 0 {
            break;
        }
        hasher.update(&buffer[..n]);
    }
    Ok(hex::encode(hasher.finalize()))
}

/// Compute the SHA-256 hash of a string and return it as a hex string.
pub fn hash_string(data: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data.as_bytes());
    hex::encode(hasher.finalize())
}

/// Capture the current environment: git revision, rustc/cargo versions, and profile.
pub fn capture_environment(profile: &str) -> Result<EnvironmentCapture> {
    let revision =
        run_cmd_stdout("git", &["rev-parse", "HEAD"]).context("failed to get git revision")?;
    let rustc_version =
        run_cmd_stdout("rustc", &["--version"]).context("failed to get rustc version")?;
    let cargo_version =
        run_cmd_stdout("cargo", &["--version"]).context("failed to get cargo version")?;

    Ok(EnvironmentCapture {
        revision,
        rustc_version,
        cargo_version,
        profile: profile.to_string(),
    })
}

/// Run a command and return its trimmed stdout.
fn run_cmd_stdout(program: &str, args: &[&str]) -> Result<String> {
    let output = Command::new(program)
        .args(args)
        .output()
        .with_context(|| format!("failed to execute: {} {}", program, args.join(" ")))?;
    if !output.status.success() {
        anyhow::bail!(
            "command failed (exit {}): {} {}\nstderr: {}",
            output.status.code().unwrap_or(-1),
            program,
            args.join(" "),
            String::from_utf8_lossy(&output.stderr)
        );
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

/// A recorder that captures test-run evidence: command, timestamps, output,
/// artifact hashes, and produces an [`EvidenceEnvelope`].
pub struct TestRunRecorder {
    work_package_id: String,
    test_id: String,
    command: String,
    requirement_ids: Vec<String>,
    environment_profile: String,
    runner_identity: String,
    independent_verifier_identity: Option<String>,
    evidence_dir: PathBuf,
}

impl TestRunRecorder {
    /// Create a new recorder.
    ///
    /// Evidence envelopes will be written to `evidence/<work_package_id>/`.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        work_package_id: &str,
        test_id: &str,
        command: &str,
        requirement_ids: Vec<String>,
        environment_profile: &str,
        runner_identity: &str,
        independent_verifier_identity: Option<String>,
        evidence_dir: &Path,
    ) -> Self {
        Self {
            work_package_id: work_package_id.to_string(),
            test_id: test_id.to_string(),
            command: command.to_string(),
            requirement_ids,
            environment_profile: environment_profile.to_string(),
            runner_identity: runner_identity.to_string(),
            independent_verifier_identity,
            evidence_dir: evidence_dir.to_path_buf(),
        }
    }

    /// Run the recorded command, capture output, and produce an evidence envelope.
    ///
    /// Returns the path to the written envelope file.
    pub fn record(&self) -> Result<(EvidenceEnvelope, PathBuf)> {
        let env_capture = capture_environment(&self.environment_profile)?;

        let started_at = rfc3339_now();
        let output = self.execute_command()?;
        let finished_at = rfc3339_now();

        let status = if output.success {
            EnvelopeStatus::ObservedPass
        } else {
            EnvelopeStatus::ObservedFail
        };

        let logs_artifact_sha256 = hash_string(&output.combined_output);
        let result_artifact_sha256 = if let Some(ref result_path) = output.result_artifact {
            hash_file(Path::new(result_path))?
        } else {
            // No separate result artifact; use the logs hash as a stand-in
            // so the field is never empty for observed evidence.
            logs_artifact_sha256.clone()
        };

        let evidence_id = uuid::Uuid::new_v4().to_string();

        let envelope = EvidenceEnvelope {
            evidence_version: "1.0.0".to_string(),
            evidence_id: evidence_id.clone(),
            work_package_id: self.work_package_id.clone(),
            requirement_ids: self.requirement_ids.clone(),
            test_ids: vec![self.test_id.clone()],
            status,
            source_revision: env_capture.revision,
            artifact_digests: HashMap::new(),
            environment_profile: self.environment_profile.clone(),
            command: self.command.clone(),
            started_at,
            finished_at,
            runner_identity: self.runner_identity.clone(),
            independent_verifier_identity: self.independent_verifier_identity.clone(),
            result_artifact_sha256,
            logs_artifact_sha256,
            exceptions: if output.success {
                vec![]
            } else {
                vec![format!(
                    "command exited with code {}",
                    output.exit_code.unwrap_or(-1)
                )]
            },
            notes: String::new(),
        };

        let envelope_path = self.write_envelope(&envelope)?;

        Ok((envelope, envelope_path))
    }

    /// Execute the command and capture stdout/stderr.
    fn execute_command(&self) -> Result<CommandOutput> {
        // Split the command string into program and args for shell-like behavior.
        let parts = shell_words(&self.command);
        if parts.is_empty() {
            anyhow::bail!("empty command string");
        }

        let program = &parts[0];
        let args: Vec<&str> = parts[1..].iter().map(|s| s.as_str()).collect();

        let output = Command::new(program)
            .args(&args)
            .output()
            .with_context(|| format!("failed to execute: {}", self.command))?;

        let combined_output = format!(
            "STDOUT:\n{}\n\nSTDERR:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );

        Ok(CommandOutput {
            success: output.status.success(),
            exit_code: output.status.code(),
            combined_output,
            result_artifact: None,
        })
    }

    /// Write the evidence envelope to `evidence/<work_package_id>/<test_id>.json`.
    fn write_envelope(&self, envelope: &EvidenceEnvelope) -> Result<PathBuf> {
        let dir = self.evidence_dir.join(&self.work_package_id);
        fs::create_dir_all(&dir)
            .with_context(|| format!("failed to create evidence directory: {}", dir.display()))?;

        let path = dir.join(format!("{}.json", self.test_id));
        let json = serde_json::to_string_pretty(envelope)
            .context("failed to serialize evidence envelope")?;
        fs::write(&path, &json)
            .with_context(|| format!("failed to write evidence envelope: {}", path.display()))?;

        eprintln!("Evidence written to {}", path.display());
        Ok(path)
    }
}

/// Output from a command execution.
struct CommandOutput {
    success: bool,
    exit_code: Option<i32>,
    combined_output: String,
    result_artifact: Option<String>,
}

/// Return the current time as an RFC 3339 string.
fn rfc3339_now() -> String {
    match SystemTime::now().duration_since(SystemTime::UNIX_EPOCH) {
        Ok(dur) => {
            let secs = dur.as_secs();
            // Simple RFC 3339 formatting without chrono dependency in xtask
            let dt = chrono::DateTime::from_timestamp(secs as i64, 0).unwrap_or_default();
            dt.to_rfc3339()
        }
        Err(_) => String::from("1970-01-01T00:00:00Z"),
    }
}

/// Simple shell-like word splitting for command strings.
/// Handles quoted strings and basic escaping.
fn shell_words(input: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut current = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let chars: Vec<char> = input.chars().collect();
    let mut i = 0;

    while i < chars.len() {
        let c = chars[i];
        if in_single {
            if c == '\'' {
                in_single = false;
            } else {
                current.push(c);
            }
        } else if in_double {
            if c == '"' {
                in_double = false;
            } else if c == '\\' && i + 1 < chars.len() {
                i += 1;
                current.push(chars[i]);
            } else {
                current.push(c);
            }
        } else if c == '\'' {
            in_single = true;
        } else if c == '"' {
            in_double = true;
        } else if c.is_whitespace() {
            if !current.is_empty() {
                words.push(current.clone());
                current.clear();
            }
        } else {
            current.push(c);
        }
        i += 1;
    }

    if !current.is_empty() {
        words.push(current);
    }

    words
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_string_produces_consistent_output() {
        let h1 = hash_string("hello world");
        let h2 = hash_string("hello world");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 64);
    }

    #[test]
    fn hash_string_different_inputs_differ() {
        let h1 = hash_string("hello");
        let h2 = hash_string("world");
        assert_ne!(h1, h2);
    }

    #[test]
    fn hash_file_produces_expected_hash() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.txt");
        fs::write(&path, "hello world").unwrap();
        let hash = hash_file(&path).unwrap();
        assert_eq!(hash, hash_string("hello world"));
    }

    #[test]
    fn hash_file_missing_file_errors() {
        let result = hash_file(Path::new("nonexistent_file_xyz.txt"));
        assert!(result.is_err());
    }

    #[test]
    fn shell_words_simple() {
        let words = shell_words("cargo test --workspace");
        assert_eq!(words, vec!["cargo", "test", "--workspace"]);
    }

    #[test]
    fn shell_words_with_quotes() {
        let words = shell_words("echo \"hello world\"");
        assert_eq!(words, vec!["echo", "hello world"]);
    }

    #[test]
    fn shell_words_empty() {
        let words = shell_words("");
        assert!(words.is_empty());
    }

    #[test]
    fn shell_words_single_quotes() {
        let words = shell_words("echo 'hello world'");
        assert_eq!(words, vec!["echo", "hello world"]);
    }
}
