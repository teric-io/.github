# teric-io/.github

Organization-level GitHub configuration for the teric-io organization.

## Contents

### Reusable Workflows

Located in `.github/workflows/`:

| Workflow | Purpose | Usage |
|----------|---------|-------|
| `infra-references-sync.yml` | Sync infrastructure references on PR merge | `uses: teric-io/.github/.github/workflows/infra-references-sync.yml@main` |
| `infra-references-reconcile.yml` | Weekly drift detection and repair | Runs on schedule (Mondays 6 AM UTC) |

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

- [PROC-007: Early Integration Bug Detection](https://github.com/teric-io/teric-planning/blob/main/PROC-007-early-integration-bug-detection.md)
- [@teric-io/contracts Package](https://github.com/teric-io/teric-contracts)

---

## Contributing

Changes to this repository affect all teric-io repositories. Please:

1. Test workflow changes in a fork first
2. Create a PR with clear description of changes
3. Get approval from platform team
4. Run validation tests locally before merging

---

*Part of [PROC-007: Early Integration Bug Detection](https://github.com/teric-io/teric-planning/blob/main/PROC-007-early-integration-bug-detection.md)*
