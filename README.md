# teric-io/.github

Organization-level GitHub configuration for the teric-io organization.

## Contents

### Reusable Workflows

Located in `.github/workflows/`:

| Workflow | Purpose | Usage |
|----------|---------|-------|
| `infra-references-sync.yml` | Retired compatibility shim for old PROC-007 callers | Existing `uses: teric-io/.github/.github/workflows/infra-references-sync.yml@main` calls succeed without syncing |
| `infra-references-reconcile.yml` | Retired manual-only compatibility shim | No scheduled reconciliation |

The PROC-007 central infra references registry was abandoned after the Phase 1
cleanup. Repositories should still keep `infra.contracts.yaml` files updated as
repo-local dependency declarations, but they should not expect a central
Lambda/DynamoDB registry sync.

---

## Workflow Templates

Located in `workflow-templates/`:

| Template | Purpose |
|----------|---------|
| `ci-lambda.yml` | Standard CI/CD for Lambda functions |
| `ci-container.yml` | Standard CI/CD for containerized apps |
| `ci-terraform.yml` | Standard CI/CD for Terraform configs |

These appear in your repository under **Actions > New workflow**.

---

## Issue Templates

Located in `ISSUE_TEMPLATE/`:

| Template | Purpose |
|----------|---------|
| `bug_report.md` | Report bugs or unexpected behavior |
| `feature_request.md` | Suggest new features |
| `agent_blocked.md` | Agent requires human approval |

---

## Organization Profile

Located in `profile/README.md` - displayed on the organization's GitHub page.

---

## Related Documentation

- [Integration Contracts Guidelines](https://github.com/teric-io/teric-meta/blob/main/guidelines/INTEGRATION_CONTRACTS_GUIDELINES.md)
- [PROC-007 historical record](https://github.com/teric-io/teric-planning/blob/main/archive/phase-1/completed/PROC-007-early-integration-bug-detection.md)

---

## Contributing

Changes to this repository affect all teric-io repositories. Please:

1. Test workflow changes in a fork first
2. Create a PR with clear description of changes
3. Get approval from platform team
4. Run validation tests locally before merging
