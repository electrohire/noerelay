use noerelay_core::iam::*;
use noerelay_store::{IamRepository, IamStoreError};
use sqlx::PgPool;
use std::collections::HashMap;
use uuid::Uuid;

async fn repository() -> IamRepository {
    let url = std::env::var("DATABASE_URL").expect("DATABASE_URL required for ignored test");
    IamRepository::new(PgPool::connect(&url).await.unwrap())
}

fn config(issuer: &str) -> OidcConfig {
    OidcConfig {
        issuer: issuer.into(),
        audience: "noerelay".into(),
        jwks_url: format!("{issuer}/jwks"),
        claim_to_scope: HashMap::from([("permissions".into(), String::new())]),
        clock_skew_seconds: 30,
        require_nonce: false,
    }
}

fn step_request(org: OrganizationId, requester: PrincipalId, action: &str) -> StepUpRequest {
    StepUpRequest {
        requester_id: requester,
        action_hash: action.into(),
        action_description: "integration test action".into(),
        scope: Scope::Organization(org),
        required_permissions: vec!["api_key:rotate".into()],
        expiry_seconds: 300,
        separation_of_duties: true,
    }
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creates_and_gets_oidc_config() {
    let repo = repository().await;
    let org = OrganizationId(Uuid::new_v4());
    let issuer = format!("https://{}.example", Uuid::new_v4());
    repo.create_oidc_config(config(&issuer), org).await.unwrap();
    assert_eq!(
        repo.get_oidc_config(org, &issuer).await.unwrap().audience,
        "noerelay"
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn lists_oidc_configs() {
    let repo = repository().await;
    let org = OrganizationId(Uuid::new_v4());
    repo.create_oidc_config(config("https://list.example"), org)
        .await
        .unwrap();
    assert_eq!(repo.list_oidc_configs(org).await.unwrap().len(), 1);
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn missing_oidc_config_is_not_found() {
    let error = repository()
        .await
        .get_oidc_config(OrganizationId(Uuid::new_v4()), "missing")
        .await
        .unwrap_err();
    assert!(matches!(error, IamStoreError::NotFound(_)));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creates_step_up_approval() {
    let repo = repository().await;
    let org = OrganizationId(Uuid::new_v4());
    let requester = PrincipalId(Uuid::new_v4());
    let approval = repo
        .create_step_up_approval(
            step_request(org, requester, &Uuid::new_v4().to_string()),
            PrincipalId(Uuid::new_v4()),
        )
        .await
        .unwrap();
    assert_eq!(approval.organization_id, org);
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn validates_step_up_approval() {
    let repo = repository().await;
    let requester = PrincipalId(Uuid::new_v4());
    let action = Uuid::new_v4().to_string();
    repo.create_step_up_approval(
        step_request(OrganizationId(Uuid::new_v4()), requester, &action),
        PrincipalId(Uuid::new_v4()),
    )
    .await
    .unwrap();
    assert_eq!(
        repo.validate_step_up_approval(&action, requester)
            .await
            .unwrap()
            .action_hash,
        action
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn rejects_non_positive_expiry() {
    let repo = repository().await;
    let requester = PrincipalId(Uuid::new_v4());
    let mut request = step_request(OrganizationId(Uuid::new_v4()), requester, "expired");
    request.expiry_seconds = 0;
    assert!(matches!(
        repo.create_step_up_approval(request, PrincipalId(Uuid::new_v4()))
            .await,
        Err(IamStoreError::StepUpExpired)
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn enforces_separation_of_duties_at_creation() {
    let repo = repository().await;
    let principal = PrincipalId(Uuid::new_v4());
    let request = step_request(OrganizationId(Uuid::new_v4()), principal, "sod");
    assert!(matches!(
        repo.create_step_up_approval(request, principal).await,
        Err(IamStoreError::SeparationOfDutiesViolation)
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn enforces_separation_of_duties_at_validation() {
    let repo = repository().await;
    let approver = PrincipalId(Uuid::new_v4());
    let action = Uuid::new_v4().to_string();
    repo.create_step_up_approval(
        step_request(
            OrganizationId(Uuid::new_v4()),
            PrincipalId(Uuid::new_v4()),
            &action,
        ),
        approver,
    )
    .await
    .unwrap();
    assert!(matches!(
        repo.validate_step_up_approval(&action, approver).await,
        Err(IamStoreError::SeparationOfDutiesViolation)
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn uses_step_up_approval_once() {
    let repo = repository().await;
    let requester = PrincipalId(Uuid::new_v4());
    let approval = repo
        .create_step_up_approval(
            step_request(
                OrganizationId(Uuid::new_v4()),
                requester,
                &Uuid::new_v4().to_string(),
            ),
            PrincipalId(Uuid::new_v4()),
        )
        .await
        .unwrap();
    repo.use_step_up_approval(approval.id).await.unwrap();
    assert!(matches!(
        repo.use_step_up_approval(approval.id).await,
        Err(IamStoreError::StepUpUnavailable)
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn revokes_step_up_approval() {
    let repo = repository().await;
    let requester = PrincipalId(Uuid::new_v4());
    let approval = repo
        .create_step_up_approval(
            step_request(
                OrganizationId(Uuid::new_v4()),
                requester,
                &Uuid::new_v4().to_string(),
            ),
            PrincipalId(Uuid::new_v4()),
        )
        .await
        .unwrap();
    repo.revoke_step_up_approval(approval.id).await.unwrap();
    assert!(matches!(
        repo.revoke_step_up_approval(approval.id).await,
        Err(IamStoreError::StepUpUnavailable)
    ));
}
