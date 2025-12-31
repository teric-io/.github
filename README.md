# teric-io/.github

Organization-level GitHub configuration for the teric-io organization.

## Contents

### Reusable Workflows

Located in `.github/workflows/`:

| Workflow | Purpose | Usage |
|----------|---------|-------|
| `contract-validation.yml` | Validate code against infrastructure contracts | `uses: teric-io/.github/.github/workflows/contract-validation.yml@main` |
| `integration-checker.yml` | Check cross-repo integration compatibility | `uses: teric-io/.github/.github/workflows/integration-checker.yml@main` |
| `required-checks.yml` | Org-wide required checks wrapper | Configured via GitHub Org Settings |

#### Contract Validation Usage

```yaml
# In your repository's .github/workflows/contract-validate.yml
name: Contract Validation

on:
  pull_request:
    paths:
      - 'src/**'
      - 'infra.contracts.yaml'

jobs:
  validate:
    uses: teric-io/.github/.github/workflows/contract-validation.yml@main
    with:
      advisory_only: false  # Set to true for warnings only
    secrets: inherit
```

### Workflow Templates

Located in `workflow-templates/`:

| Template | Purpose |
|----------|---------|
| `ci-lambda.yml` | Standard CI/CD for Lambda functions |
| `ci-container.yml` | Standard CI/CD for containerized apps |
| `ci-terraform.yml` | Standard CI/CD for Terraform configs |

These appear in your repository under **Actions > New workflow**.

### Issue Templates

Located in `ISSUE_TEMPLATE/`:

| Template | Purpose |
|----------|---------|
| `bug_report.md` | Report bugs or unexpected behavior |
| `feature_request.md` | Suggest new features |
| `agent_blocked.md` | Agent requires human approval |

### Organization Profile

Located in `profile/README.md` - displayed on the organization's GitHub page.

## Related Documentation

- [PROC-007: Early Integration Bug Detection](https://github.com/teric-io/teric-planning/blob/main/PROC-007-early-integration-bug-detection.md)
- [Teric Meta Guidelines](https://github.com/teric-io/teric-meta)
- [@teric/contracts Package](https://github.com/teric-io/teric-contracts)

## Configuration

### Enabling Required Workflows

1. Go to **GitHub Org Settings > Actions > Workflows**
2. Under "Required workflows", click **Add workflow**
3. Select `teric-io/.github` repository
4. Choose `required-checks.yml`
5. Select target repositories (all or specific)

### Bypass Mechanisms

For emergency situations, add these labels to PRs:

| Label | Effect |
|-------|--------|
| `contract-bypass` | Skip contract validation entirely |
| `contract-advisory` | Run validation in advisory (non-blocking) mode |

## Contributing

Changes to this repository affect all teric-io repositories. Please:

1. Test workflow changes in a fork first
2. Create a PR with clear description of changes
3. Get approval from platform team

---

*Part of [PROC-007: Early Integration Bug Detection](https://github.com/teric-io/teric-planning/blob/main/PROC-007-early-integration-bug-detection.md)*
