"""Role-Based Access Control (RBAC) for NoeRelay.

Maps API endpoints to required permissions and checks that the
authenticated user's role has those permissions.

Supports five roles: admin, operator, auditor, developer, viewer.
Each role has a defined set of permissions covering all API endpoints.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"        # Full access to everything
    OPERATOR = "operator"  # Read + execute (chat, benchmarks, model management)
    AUDITOR = "auditor"    # Read-only access to ledger, receipts, analytics, audit log
    DEVELOPER = "developer" # Canary traffic + benchmark execution only
    VIEWER = "viewer"      # Read-only access to models, health, metrics


class Permission(str, Enum):
    # Chat/inference
    CHAT_COMPLETIONS = "chat.completions"
    RESPONSES_API = "responses.api"
    # Model management
    MODELS_READ = "models.read"
    MODELS_WRITE = "models.write"  # pull, remove, register
    MODELS_LIFECYCLE = "models.lifecycle"  # recommendations, ranking
    # Benchmark
    BENCHMARKS_READ = "benchmarks.read"
    BENCHMARKS_WRITE = "benchmarks.write"  # run benchmarks
    # Governance
    GOVERNANCE_READ = "governance.read"
    GOVERNANCE_WRITE = "governance.write"  # update policy, risk classes
    # Routing
    ROUTING_READ = "routing.read"
    ROUTING_WRITE = "routing.write"  # add/remove/update candidates
    # API keys
    API_KEYS_READ = "api_keys.read"
    API_KEYS_WRITE = "api_keys.write"  # create, revoke, rotate
    # Analytics
    ANALYTICS_READ = "analytics.read"
    # Export/Import
    EXPORT = "export"
    IMPORT = "import"
    # Admin
    ADMIN_BACKUP = "admin.backup"
    ADMIN_RESTORE = "admin.restore"
    # Ledger
    LEDGER_READ = "ledger.read"
    LEDGER_VERIFY = "ledger.verify"
    # Audit
    AUDIT_READ = "audit.read"
    # Tenants
    TENANTS_READ = "tenants.read"
    TENANTS_WRITE = "tenants.write"
    # Alerts
    ALERTS_READ = "alerts.read"
    ALERTS_WRITE = "alerts.write"
    # Webhooks
    WEBHOOKS_READ = "webhooks.read"
    WEBHOOKS_WRITE = "webhooks.write"
    # Config
    CONFIG_READ = "config.read"
    CONFIG_WRITE = "config.write"
    # Secrets
    SECRETS_READ = "secrets.read"
    SECRETS_WRITE = "secrets.write"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),  # All permissions
    Role.OPERATOR: {
        Permission.CHAT_COMPLETIONS, Permission.RESPONSES_API,
        Permission.MODELS_READ, Permission.MODELS_WRITE, Permission.MODELS_LIFECYCLE,
        Permission.BENCHMARKS_READ, Permission.BENCHMARKS_WRITE,
        Permission.GOVERNANCE_READ, Permission.ROUTING_READ,
        Permission.LEDGER_READ,
    },
    Role.AUDITOR: {
        Permission.MODELS_READ, Permission.BENCHMARKS_READ,
        Permission.GOVERNANCE_READ, Permission.ROUTING_READ,
        Permission.ANALYTICS_READ, Permission.EXPORT,
        Permission.LEDGER_READ, Permission.LEDGER_VERIFY,
        Permission.AUDIT_READ,
        Permission.TENANTS_READ, Permission.ALERTS_READ,
        Permission.WEBHOOKS_READ, Permission.CONFIG_READ,
    },
    Role.DEVELOPER: {
        Permission.CHAT_COMPLETIONS, Permission.RESPONSES_API,
        Permission.BENCHMARKS_READ, Permission.BENCHMARKS_WRITE,
        Permission.MODELS_READ, Permission.GOVERNANCE_READ,
        Permission.ROUTING_READ,
    },
    Role.VIEWER: {
        Permission.MODELS_READ, Permission.GOVERNANCE_READ,
        Permission.ROUTING_READ,
    },
}


class RBACMiddleware:
    """Role-based access control middleware.

    Maps API endpoints to required permissions and checks
    that the authenticated user's role has those permissions.
    """

    # Map route patterns to required permissions
    ROUTE_PERMISSIONS: dict[tuple[str, str], list[Permission]] = {
        # Chat/inference
        ("POST", "/v1/chat/completions"): [Permission.CHAT_COMPLETIONS],
        ("POST", "/v1/responses"): [Permission.RESPONSES_API],
        # Models
        ("GET", "/v1/models"): [Permission.MODELS_READ],
        ("GET", "/models/local"): [Permission.MODELS_READ],
        ("GET", "/models/recommendations"): [Permission.MODELS_LIFECYCLE],
        ("GET", "/models/cloud"): [Permission.MODELS_READ],
        ("GET", "/models/ranking"): [Permission.MODELS_LIFECYCLE],
        ("POST", "/v1/models/pull"): [Permission.MODELS_WRITE],
        ("DELETE", "/v1/models"): [Permission.MODELS_WRITE],
        ("POST", "/v1/models/register"): [Permission.MODELS_WRITE],
        # Benchmarks
        ("POST", "/v1/benchmarks/run"): [Permission.BENCHMARKS_WRITE],
        ("GET", "/v1/benchmarks/results"): [Permission.BENCHMARKS_READ],
        ("GET", "/v1/benchmarks/compare"): [Permission.BENCHMARKS_READ],
        # Governance
        ("GET", "/v1/governance/policy"): [Permission.GOVERNANCE_READ],
        ("POST", "/v1/governance/policy"): [Permission.GOVERNANCE_WRITE],
        ("PUT", "/v1/governance/policy"): [Permission.GOVERNANCE_WRITE],
        ("GET", "/v1/governance/risk-classes"): [Permission.GOVERNANCE_READ],
        ("PUT", "/v1/governance/risk-class"): [Permission.GOVERNANCE_WRITE],
        # Routing
        ("GET", "/v1/routing/portfolio"): [Permission.ROUTING_READ],
        ("POST", "/v1/routing/candidates"): [Permission.ROUTING_WRITE],
        ("DELETE", "/v1/routing/candidates"): [Permission.ROUTING_WRITE],
        ("PUT", "/v1/routing/candidates"): [Permission.ROUTING_WRITE],
        # API Keys
        ("POST", "/v1/api-keys"): [Permission.API_KEYS_WRITE],
        ("GET", "/v1/api-keys"): [Permission.API_KEYS_READ],
        ("DELETE", "/v1/api-keys"): [Permission.API_KEYS_WRITE],
        # Analytics
        ("GET", "/v1/analytics/cost"): [Permission.ANALYTICS_READ],
        ("GET", "/v1/analytics/performance"): [Permission.ANALYTICS_READ],
        ("GET", "/v1/analytics/usage"): [Permission.ANALYTICS_READ],
        ("GET", "/v1/analytics/escalations"): [Permission.ANALYTICS_READ],
        ("GET", "/v1/analytics/audit"): [Permission.AUDIT_READ],
        ("GET", "/v1/analytics/benchmarks"): [Permission.ANALYTICS_READ],
        ("GET", "/v1/analytics/dashboard"): [Permission.ANALYTICS_READ],
        # Export/Import
        ("GET", "/v1/export"): [Permission.EXPORT],
        ("POST", "/v1/import"): [Permission.IMPORT],
        # Admin
        ("POST", "/v1/admin/backup"): [Permission.ADMIN_BACKUP],
        ("POST", "/v1/admin/restore"): [Permission.ADMIN_RESTORE],
        ("GET", "/v1/admin/export"): [Permission.EXPORT],
        # Ledger
        ("GET", "/v1/epr/ledger/events"): [Permission.LEDGER_READ],
        ("GET", "/v1/epr/ledger/chain"): [Permission.LEDGER_READ],
        ("POST", "/v1/epr/ledger/verify"): [Permission.LEDGER_VERIFY],
        ("GET", "/v1/epr/ledger/export"): [Permission.LEDGER_READ],
        ("GET", "/v1/epr/runs"): [Permission.LEDGER_READ],
        ("GET", "/v1/epr/runs/trace"): [Permission.LEDGER_READ],
        # Tenants
        ("GET", "/v1/tenants"): [Permission.TENANTS_READ],
        ("POST", "/v1/tenants"): [Permission.TENANTS_WRITE],
        ("PUT", "/v1/tenants"): [Permission.TENANTS_WRITE],
        ("DELETE", "/v1/tenants"): [Permission.TENANTS_WRITE],
        ("GET", "/v1/tenants/budget"): [Permission.TENANTS_READ],
        # Alerts
        ("GET", "/v1/alerts"): [Permission.ALERTS_READ],
        ("POST", "/v1/alerts/acknowledge"): [Permission.ALERTS_WRITE],
        ("POST", "/v1/alerts/rules"): [Permission.ALERTS_WRITE],
        ("POST", "/v1/alerts"): [Permission.ALERTS_WRITE],
        # Webhooks
        ("GET", "/v1/webhooks"): [Permission.WEBHOOKS_READ],
        ("POST", "/v1/webhooks"): [Permission.WEBHOOKS_WRITE],
        ("DELETE", "/v1/webhooks"): [Permission.WEBHOOKS_WRITE],
        # Config
        ("GET", "/v1/config"): [Permission.CONFIG_READ],
        ("PUT", "/v1/config"): [Permission.CONFIG_WRITE],
        # Secrets
        ("GET", "/v1/secrets"): [Permission.SECRETS_READ],
        ("POST", "/v1/secrets"): [Permission.SECRETS_WRITE],
        ("DELETE", "/v1/secrets"): [Permission.SECRETS_WRITE],
        # System (no auth required)
        ("GET", "/health"): [],
        ("GET", "/ready"): [],
        ("GET", "/metrics"): [Permission.ANALYTICS_READ],
        ("GET", "/cache/stats"): [Permission.ANALYTICS_READ],
        ("GET", "/dashboard"): [Permission.ANALYTICS_READ],
    }

    def check_permission(self, method: str, path: str, role: str | None) -> tuple[bool, str | None]:
        """Check if the role has permission for the route.

        Returns (allowed, reason).
        """
        if role is None:
            return True, None  # No auth required (open access mode)

        try:
            role_enum = Role(role)
        except ValueError:
            return False, f"unknown role '{role}'"

        required = self._get_required_permissions(method, path)
        if required is None:
            if role_enum is Role.ADMIN:
                return True, None
            return False, "route is not available to this role"
        if not required:
            return True, None  # Explicitly public route (e.g., /health)

        role_perms = ROLE_PERMISSIONS.get(role_enum, set())
        missing = required - role_perms
        if missing:
            return False, f"role '{role}' lacks permissions: {', '.join(p.value for p in missing)}"
        return True, None

    def _get_required_permissions(
        self, method: str, path: str
    ) -> set[Permission] | None:
        """Get required permissions for a route."""
        # Try exact match first
        key = (method, path)
        if key in self.ROUTE_PERMISSIONS:
            return set(self.ROUTE_PERMISSIONS[key])

        # Try prefix match (for path parameters)
        for (m, prefix), perms in self.ROUTE_PERMISSIONS.items():
            if m == method and path.startswith(prefix) and prefix != path:
                # Ensure we match a full path segment (e.g., /v1/tenants/abc matches /v1/tenants)
                remaining = path[len(prefix):]
                if remaining.startswith("/") or not remaining:
                    return set(perms)

        return None  # Unmapped routes are denied to non-admin identities.
