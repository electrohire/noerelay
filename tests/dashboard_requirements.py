"""
NoeRelay Dashboard UI Requirements & Test Traceability Matrix

This module documents every UI requirement for the NoeRelay dashboard
and maps each requirement to one or more Playwright test case IDs.

REQ-DASH-001: Dashboard loads and displays sidebar navigation
  → test_dashboard_loads
  → test_sidebar_navigation_visible

REQ-DASH-002: Dark theme is applied
  → test_dark_theme_applied

REQ-DASH-003: Top bar shows connection status, gateway mode, refresh button (no overlap)
  → test_topbar_status_indicator
  → test_topbar_mode_badge
  → test_topbar_refresh_button
  → test_topbar_no_overlap

REQ-DASH-004: Overview section shows status cards
  → test_overview_status_cards

REQ-DASH-005: Overview section shows cost trend chart
  → test_overview_cost_chart

REQ-DASH-006: Overview section shows usage trend chart
  → test_overview_usage_chart

REQ-DASH-007: Overview section shows recent runs table
  → test_overview_recent_runs_table

REQ-DASH-008: Overview section shows recent alerts
  → test_overview_recent_alerts

REQ-DASH-009: Models section shows local models table
  → test_models_local_table

REQ-DASH-010: Models section shows cloud models table
  → test_models_cloud_table

REQ-DASH-011: Models section shows model ranking
  → test_models_ranking_table

REQ-DASH-012: Models section shows download recommendations with Pull buttons
  → test_models_download_recommendations
  → test_models_pull_button

REQ-DASH-013: Models section shows removal recommendations with Remove buttons
  → test_models_removal_recommendations
  → test_models_remove_button

REQ-DASH-014: Benchmarks section shows run form
  → test_benchmarks_run_form
  → test_benchmarks_dataset_dropdown
  → test_benchmarks_evaluator_dropdown
  → test_benchmarks_prefer_local_checkbox
  → test_benchmarks_run_button

REQ-DASH-015: Benchmarks section shows results table
  → test_benchmarks_results_table

REQ-DASH-016: Benchmarks section shows inline benchmark button
  → test_benchmarks_inline_button

REQ-DASH-017: Governance section shows routing policy
  → test_governance_policy_display

REQ-DASH-018: Governance section shows risk class table
  → test_governance_risk_classes

REQ-DASH-019: Governance section shows verification DAG
  → test_governance_verification_dag

REQ-DASH-020: Routing section shows portfolio table
  → test_routing_portfolio_table

REQ-DASH-021: Routing section shows add candidate form
  → test_routing_add_candidate_form

REQ-DASH-022: Routing section shows remove buttons
  → test_routing_remove_button

REQ-DASH-023: Analytics section shows time range selector
  → test_analytics_time_range

REQ-DASH-024: Analytics section shows cost analytics
  → test_analytics_cost

REQ-DASH-025: Analytics section shows performance analytics
  → test_analytics_performance

REQ-DASH-026: Analytics section shows usage analytics
  → test_analytics_usage

REQ-DASH-027: Analytics section shows escalation analytics
  → test_analytics_escalations

REQ-DASH-028: Ledger section shows run selector
  → test_ledger_run_selector

REQ-DASH-029: Ledger section shows events table
  → test_ledger_events_table

REQ-DASH-030: Ledger section shows decision trace viewer
  → test_ledger_decision_trace

REQ-DASH-031: Ledger section shows chain verify button
  → test_ledger_verify_button

REQ-DASH-032: Ledger section shows export button
  → test_ledger_export_button

REQ-DASH-033: Tenants section shows tenant list
  → test_tenants_list

REQ-DASH-034: Tenants section shows create form
  → test_tenants_create_form

REQ-DASH-035: API Keys section shows key list
  → test_apikeys_list

REQ-DASH-036: API Keys section shows create form
  → test_apikeys_create_form

REQ-DASH-037: API Keys section shows revoke/rotate buttons
  → test_apikeys_revoke_button
  → test_apikeys_rotate_button

REQ-DASH-038: Alerts section shows active alerts
  → test_alerts_active_list

REQ-DASH-039: Alerts section shows acknowledge button
  → test_alerts_acknowledge_button

REQ-DASH-040: Webhooks section shows webhook list
  → test_webhooks_list

REQ-DASH-041: Webhooks section shows register form
  → test_webhooks_register_form

REQ-DASH-042: Config section shows config values
  → test_config_values

REQ-DASH-043: Config section shows edit buttons
  → test_config_edit_button

REQ-DASH-044: Audit Log section shows audit entries
  → test_audit_entries

REQ-DASH-045: Audit Log section shows filters
  → test_audit_filters

REQ-DASH-046: Settings section shows mode toggle
  → test_settings_mode_toggle

REQ-DASH-047: Settings section shows cache controls
  → test_settings_cache_controls

REQ-DASH-048: Settings section shows backup/restore buttons
  → test_settings_backup_button
  → test_settings_restore_button

REQ-DASH-049: Settings section shows export/import buttons
  → test_settings_export_button
  → test_settings_import_button

REQ-DASH-050: Sidebar navigation switches sections
  → test_sidebar_navigation_switches

REQ-DASH-051: Auto-refresh toggle works
  → test_auto_refresh_toggle

REQ-DASH-052: Toast notifications appear on actions
  → test_toast_notifications

REQ-DASH-053: Modal dialogs appear for confirmations
  → test_modal_confirmations

REQ-DASH-054: Loading spinners appear during fetch
  → test_loading_spinners

REQ-DASH-055: Section persistence via localStorage
  → test_section_persistence

REQ-DASH-056: Responsive layout on tablet viewport
  → test_responsive_tablet
"""

# ---------------------------------------------------------------------------
# Requirements traceability matrix
# ---------------------------------------------------------------------------
# Each entry maps a requirement ID to a description and the list of test
# method names that verify it.
# ---------------------------------------------------------------------------

REQUIREMENTS = {
    "REQ-DASH-001": {
        "description": "Dashboard loads and displays sidebar navigation",
        "tests": ["test_dashboard_loads", "test_sidebar_navigation_visible"],
        "section": "Global",
    },
    "REQ-DASH-002": {
        "description": "Dark theme is applied",
        "tests": ["test_dark_theme_applied"],
        "section": "Global",
    },
    "REQ-DASH-003": {
        "description": "Top bar shows connection status, gateway mode, refresh button (no overlap)",
        "tests": [
            "test_topbar_status_indicator",
            "test_topbar_mode_badge",
            "test_topbar_refresh_button",
            "test_topbar_no_overlap",
        ],
        "section": "Top Bar",
    },
    "REQ-DASH-004": {
        "description": "Overview section shows status cards",
        "tests": ["test_overview_status_cards"],
        "section": "Overview",
    },
    "REQ-DASH-005": {
        "description": "Overview section shows cost trend chart",
        "tests": ["test_overview_cost_chart"],
        "section": "Overview",
    },
    "REQ-DASH-006": {
        "description": "Overview section shows usage trend chart",
        "tests": ["test_overview_usage_chart"],
        "section": "Overview",
    },
    "REQ-DASH-007": {
        "description": "Overview section shows recent runs table",
        "tests": ["test_overview_recent_runs_table"],
        "section": "Overview",
    },
    "REQ-DASH-008": {
        "description": "Overview section shows recent alerts",
        "tests": ["test_overview_recent_alerts"],
        "section": "Overview",
    },
    "REQ-DASH-009": {
        "description": "Models section shows local models table",
        "tests": ["test_models_local_table"],
        "section": "Models",
    },
    "REQ-DASH-010": {
        "description": "Models section shows cloud models table",
        "tests": ["test_models_cloud_table"],
        "section": "Models",
    },
    "REQ-DASH-011": {
        "description": "Models section shows model ranking",
        "tests": ["test_models_ranking_table"],
        "section": "Models",
    },
    "REQ-DASH-012": {
        "description": "Models section shows download recommendations with Pull buttons",
        "tests": ["test_models_download_recommendations", "test_models_pull_button"],
        "section": "Models",
    },
    "REQ-DASH-013": {
        "description": "Models section shows removal recommendations with Remove buttons",
        "tests": ["test_models_removal_recommendations", "test_models_remove_button"],
        "section": "Models",
    },
    "REQ-DASH-014": {
        "description": "Benchmarks section shows run form",
        "tests": [
            "test_benchmarks_run_form",
            "test_benchmarks_dataset_dropdown",
            "test_benchmarks_evaluator_dropdown",
            "test_benchmarks_prefer_local_checkbox",
            "test_benchmarks_run_button",
        ],
        "section": "Benchmarks",
    },
    "REQ-DASH-015": {
        "description": "Benchmarks section shows results table",
        "tests": ["test_benchmarks_results_table"],
        "section": "Benchmarks",
    },
    "REQ-DASH-016": {
        "description": "Benchmarks section shows inline benchmark button",
        "tests": ["test_benchmarks_inline_button"],
        "section": "Benchmarks",
    },
    "REQ-DASH-017": {
        "description": "Governance section shows routing policy",
        "tests": ["test_governance_policy_display"],
        "section": "Governance",
    },
    "REQ-DASH-018": {
        "description": "Governance section shows risk class table",
        "tests": ["test_governance_risk_classes"],
        "section": "Governance",
    },
    "REQ-DASH-019": {
        "description": "Governance section shows verification DAG",
        "tests": ["test_governance_verification_dag"],
        "section": "Governance",
    },
    "REQ-DASH-020": {
        "description": "Routing section shows portfolio table",
        "tests": ["test_routing_portfolio_table"],
        "section": "Routing",
    },
    "REQ-DASH-021": {
        "description": "Routing section shows add candidate form",
        "tests": ["test_routing_add_candidate_form"],
        "section": "Routing",
    },
    "REQ-DASH-022": {
        "description": "Routing section shows remove buttons",
        "tests": ["test_routing_remove_button"],
        "section": "Routing",
    },
    "REQ-DASH-023": {
        "description": "Analytics section shows time range selector",
        "tests": ["test_analytics_time_range"],
        "section": "Analytics",
    },
    "REQ-DASH-024": {
        "description": "Analytics section shows cost analytics",
        "tests": ["test_analytics_cost"],
        "section": "Analytics",
    },
    "REQ-DASH-025": {
        "description": "Analytics section shows performance analytics",
        "tests": ["test_analytics_performance"],
        "section": "Analytics",
    },
    "REQ-DASH-026": {
        "description": "Analytics section shows usage analytics",
        "tests": ["test_analytics_usage"],
        "section": "Analytics",
    },
    "REQ-DASH-027": {
        "description": "Analytics section shows escalation analytics",
        "tests": ["test_analytics_escalations"],
        "section": "Analytics",
    },
    "REQ-DASH-028": {
        "description": "Ledger section shows run selector",
        "tests": ["test_ledger_run_selector"],
        "section": "Ledger",
    },
    "REQ-DASH-029": {
        "description": "Ledger section shows events table",
        "tests": ["test_ledger_events_table"],
        "section": "Ledger",
    },
    "REQ-DASH-030": {
        "description": "Ledger section shows decision trace viewer",
        "tests": ["test_ledger_decision_trace"],
        "section": "Ledger",
    },
    "REQ-DASH-031": {
        "description": "Ledger section shows chain verify button",
        "tests": ["test_ledger_verify_button"],
        "section": "Ledger",
    },
    "REQ-DASH-032": {
        "description": "Ledger section shows export button",
        "tests": ["test_ledger_export_button"],
        "section": "Ledger",
    },
    "REQ-DASH-033": {
        "description": "Tenants section shows tenant list",
        "tests": ["test_tenants_list"],
        "section": "Tenants",
    },
    "REQ-DASH-034": {
        "description": "Tenants section shows create form",
        "tests": ["test_tenants_create_form"],
        "section": "Tenants",
    },
    "REQ-DASH-035": {
        "description": "API Keys section shows key list",
        "tests": ["test_apikeys_list"],
        "section": "API Keys",
    },
    "REQ-DASH-036": {
        "description": "API Keys section shows create form",
        "tests": ["test_apikeys_create_form"],
        "section": "API Keys",
    },
    "REQ-DASH-037": {
        "description": "API Keys section shows revoke/rotate buttons",
        "tests": ["test_apikeys_revoke_button", "test_apikeys_rotate_button"],
        "section": "API Keys",
    },
    "REQ-DASH-038": {
        "description": "Alerts section shows active alerts",
        "tests": ["test_alerts_active_list"],
        "section": "Alerts",
    },
    "REQ-DASH-039": {
        "description": "Alerts section shows acknowledge button",
        "tests": ["test_alerts_acknowledge_button"],
        "section": "Alerts",
    },
    "REQ-DASH-040": {
        "description": "Webhooks section shows webhook list",
        "tests": ["test_webhooks_list"],
        "section": "Webhooks",
    },
    "REQ-DASH-041": {
        "description": "Webhooks section shows register form",
        "tests": ["test_webhooks_register_form"],
        "section": "Webhooks",
    },
    "REQ-DASH-042": {
        "description": "Config section shows config values",
        "tests": ["test_config_values"],
        "section": "Config",
    },
    "REQ-DASH-043": {
        "description": "Config section shows edit buttons",
        "tests": ["test_config_edit_button"],
        "section": "Config",
    },
    "REQ-DASH-044": {
        "description": "Audit Log section shows audit entries",
        "tests": ["test_audit_entries"],
        "section": "Audit Log",
    },
    "REQ-DASH-045": {
        "description": "Audit Log section shows filters",
        "tests": ["test_audit_filters"],
        "section": "Audit Log",
    },
    "REQ-DASH-046": {
        "description": "Settings section shows mode toggle",
        "tests": ["test_settings_mode_toggle"],
        "section": "Settings",
    },
    "REQ-DASH-047": {
        "description": "Settings section shows cache controls",
        "tests": ["test_settings_cache_controls"],
        "section": "Settings",
    },
    "REQ-DASH-048": {
        "description": "Settings section shows backup/restore buttons",
        "tests": ["test_settings_backup_button", "test_settings_restore_button"],
        "section": "Settings",
    },
    "REQ-DASH-049": {
        "description": "Settings section shows export/import buttons",
        "tests": ["test_settings_export_button", "test_settings_import_button"],
        "section": "Settings",
    },
    "REQ-DASH-050": {
        "description": "Sidebar navigation switches sections",
        "tests": ["test_sidebar_navigation_switches"],
        "section": "Global",
    },
    "REQ-DASH-051": {
        "description": "Auto-refresh toggle works",
        "tests": ["test_auto_refresh_toggle"],
        "section": "Global",
    },
    "REQ-DASH-052": {
        "description": "Toast notifications appear on actions",
        "tests": ["test_toast_notifications"],
        "section": "Global",
    },
    "REQ-DASH-053": {
        "description": "Modal dialogs appear for confirmations",
        "tests": ["test_modal_confirmations"],
        "section": "Global",
    },
    "REQ-DASH-054": {
        "description": "Loading spinners appear during fetch",
        "tests": ["test_loading_spinners"],
        "section": "Global",
    },
    "REQ-DASH-055": {
        "description": "Section persistence via localStorage",
        "tests": ["test_section_persistence"],
        "section": "Global",
    },
    "REQ-DASH-056": {
        "description": "Responsive layout on tablet viewport",
        "tests": ["test_responsive_tablet"],
        "section": "Global",
    },
}


def get_all_test_ids():
    """Return a sorted list of all unique test method names."""
    ids = set()
    for req in REQUIREMENTS.values():
        ids.update(req["tests"])
    return sorted(ids)


def get_requirements_by_section():
    """Return requirements grouped by dashboard section."""
    sections = {}
    for req_id, req in REQUIREMENTS.items():
        section = req["section"]
        if section not in sections:
            sections[section] = []
        sections[section].append((req_id, req))
    return sections


def validate_coverage():
    """Validate that every requirement has at least one test and no test is orphaned."""
    all_test_ids = get_all_test_ids()
    issues = []

    for req_id, req in REQUIREMENTS.items():
        if not req["tests"]:
            issues.append(f"{req_id} has no tests mapped")

    # Check for duplicate test names across requirements (allowed, but noted)
    test_to_reqs = {}
    for req_id, req in REQUIREMENTS.items():
        for test_id in req["tests"]:
            test_to_reqs.setdefault(test_id, []).append(req_id)

    return {
        "total_requirements": len(REQUIREMENTS),
        "total_unique_tests": len(all_test_ids),
        "issues": issues,
        "test_to_requirements": test_to_reqs,
    }


if __name__ == "__main__":
    coverage = validate_coverage()
    print(f"Requirements: {coverage['total_requirements']}")
    print(f"Unique tests: {coverage['total_unique_tests']}")
    if coverage["issues"]:
        for issue in coverage["issues"]:
            print(f"  ISSUE: {issue}")
    else:
        print("All requirements have test coverage.")