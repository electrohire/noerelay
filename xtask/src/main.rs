use clap::{Parser, Subcommand};
use std::path::Path;

mod coverage;
mod evidence;
mod golden;
mod schema;
mod validate;

#[derive(Parser)]
#[command(name = "xtask", about = "NoeRelay build automation and schema tooling")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Schema generation and validation commands
    #[command(subcommand)]
    Schema(SchemaCommand),
    /// Evidence collection, validation, and coverage commands
    #[command(subcommand)]
    Evidence(EvidenceCommand),
}

#[derive(Subcommand)]
enum SchemaCommand {
    /// Generate JSON Schemas from Rust types into spec/schemas/generated/
    Json,
    /// Generate/merge OpenAPI components from Rust types
    Openapi,
    /// Check for breaking schema changes vs committed schemas
    Diff,
    /// Run golden vector round-trip tests
    Golden,
}

#[derive(Subcommand)]
enum EvidenceCommand {
    /// Record evidence for a test execution
    Record {
        /// Work package ID (e.g., FND-03)
        work_package_id: String,
        /// Test ID (e.g., T-SPEC-001)
        test_id: String,
        /// Command to execute (e.g., "cargo test --workspace")
        command: String,
        /// Requirement IDs satisfied by this evidence (comma-separated)
        #[arg(long, value_delimiter = ',')]
        requirements: Vec<String>,
        /// Environment profile name
        #[arg(long, default_value = "single-region-org-v1-local-test")]
        profile: String,
        /// Runner identity
        #[arg(long, default_value = "ROLE-RUST")]
        runner: String,
        /// Independent verifier identity
        #[arg(long)]
        verifier: Option<String>,
        /// Evidence directory
        #[arg(long, default_value = "evidence")]
        evidence_dir: String,
    },
    /// Validate all evidence bundles
    Validate {
        /// Evidence directory
        #[arg(long, default_value = "evidence")]
        evidence_dir: String,
        /// Coverage manifest path
        #[arg(long, default_value = "spec/coverage-manifest.json")]
        manifest: String,
    },
    /// Generate requirement coverage report
    Coverage {
        /// Evidence directory
        #[arg(long, default_value = "evidence")]
        evidence_dir: String,
        /// Coverage manifest path
        #[arg(long, default_value = "spec/coverage-manifest.json")]
        manifest: String,
    },
    /// Check if a specific release gate has all required evidence
    Gate {
        /// Gate ID (G0-G8)
        gate_id: String,
        /// Evidence directory
        #[arg(long, default_value = "evidence")]
        evidence_dir: String,
        /// Coverage manifest path
        #[arg(long, default_value = "spec/coverage-manifest.json")]
        manifest: String,
    },
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Schema(cmd) => match cmd {
            SchemaCommand::Json => schema::generate_json(),
            SchemaCommand::Openapi => schema::generate_openapi(),
            SchemaCommand::Diff => schema::diff(),
            SchemaCommand::Golden => golden::run(),
        },
        Commands::Evidence(cmd) => match cmd {
            EvidenceCommand::Record {
                work_package_id,
                test_id,
                command,
                requirements,
                profile,
                runner,
                verifier,
                evidence_dir,
            } => {
                let recorder = evidence::TestRunRecorder::new(
                    &work_package_id,
                    &test_id,
                    &command,
                    requirements,
                    &profile,
                    &runner,
                    verifier,
                    Path::new(&evidence_dir),
                );
                let (_envelope, path) = recorder.record()?;
                println!("Evidence recorded to {}", path.display());
                Ok(())
            }
            EvidenceCommand::Validate {
                evidence_dir,
                manifest,
            } => {
                let manifest_path = Path::new(&manifest);
                let (known_reqs, known_tests) = if manifest_path.exists() {
                    load_known_from_manifest(manifest_path)?
                } else {
                    (Default::default(), Default::default())
                };

                let validator = validate::BundleValidator::new(
                    Path::new(&evidence_dir),
                    known_reqs,
                    known_tests,
                );
                let report = validator.validate()?;
                validate::print_report(&report);
                if !report.passed {
                    std::process::exit(1);
                }
                Ok(())
            }
            EvidenceCommand::Coverage {
                evidence_dir,
                manifest,
            } => {
                let report = coverage::generate_coverage_report(
                    Path::new(&manifest),
                    Path::new(&evidence_dir),
                )?;
                coverage::print_coverage_report(&report);
                if !report.passed {
                    std::process::exit(1);
                }
                Ok(())
            }
            EvidenceCommand::Gate {
                gate_id,
                evidence_dir,
                manifest,
            } => {
                let passed =
                    coverage::check_gate(&gate_id, Path::new(&manifest), Path::new(&evidence_dir))?;
                if !passed {
                    std::process::exit(1);
                }
                Ok(())
            }
        },
    }
}

/// Load known requirement and test IDs from the coverage manifest.
fn load_known_from_manifest(
    manifest_path: &Path,
) -> anyhow::Result<(
    std::collections::BTreeSet<String>,
    std::collections::BTreeSet<String>,
)> {
    let content = std::fs::read_to_string(manifest_path)?;
    let parsed: serde_json::Value = serde_json::from_str(&content)?;

    let mut reqs = std::collections::BTreeSet::new();
    let mut tests = std::collections::BTreeSet::new();

    if let Some(req_array) = parsed.get("requirements").and_then(|v| v.as_array()) {
        for req in req_array {
            if let Some(id) = req.get("requirement_id").and_then(|v| v.as_str()) {
                reqs.insert(id.to_string());
            }
            if let Some(test_array) = req.get("primary_release_tests").and_then(|v| v.as_array()) {
                for test in test_array {
                    if let Some(tid) = test.as_str() {
                        tests.insert(tid.to_string());
                    }
                }
            }
        }
    }

    Ok((reqs, tests))
}
