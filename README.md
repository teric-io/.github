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

### Validation Scripts

Located in `.github/scripts/`:

| Script | Purpose |
|--------|---------|
| `validate-contracts.js` | Scans source code for hardcoded infrastructure references |

---

## Contract Validation

The contract validation workflow checks your code against infrastructure contracts to catch integration bugs early.

### Quick Start

1. **Create `infra.contracts.yaml`** in your repository root:

```yaml
contract_version: "1.0"
repository: "teric-io/your-repo"
updated_at: "2025-01-01"

contracts:
  source: "@teric/contracts"
  tag: stable

dynamodb:
  tables:
    - name: work-items
      contract: "@teric/contracts/dynamodb/work-items"
      operations: [GetItem, PutItem, Query]
```

2. **Add workflow** to `.github/workflows/contract-validate.yml`:

```yaml
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
      advisory_only: false
    secrets: inherit
```

### Workflow Inputs

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `advisory_only` | boolean | `false` | Run in advisory mode (warnings only, no failure) |
| `contracts_tag` | string | `stable` | Version tag of @teric/contracts to use |
| `node_version` | string | `20` | Node.js version for validation |

### Workflow Outputs

| Output | Description |
|--------|-------------|
| `validation_passed` | `true` if validation passed, `false` otherwise |
| `violations_count` | Number of contract violations found |

### What Gets Validated

The validation script scans for:

| Pattern | Severity | Example |
|---------|----------|---------|
| Hardcoded DynamoDB table names | Error | `'teric-agentic-work-items'` |
| Hardcoded Lambda function names | Error | `FunctionName: 'app-code-review'` |
| Hardcoded EventBridge sources | Warning | `Source: 'teric.orchestrator'` |

**Instead of:**
```typescript
const TABLE_NAME = 'teric-agentic-work-items';
```

**Use:**
```typescript
import { WorkItemsTable } from '@teric/contracts/dynamodb';
const TABLE_NAME = WorkItemsTable.name;
```

### Advisory vs Blocking Mode

| Mode | Violations | Exit Code | PR Comment |
|------|------------|-----------|------------|
| **Blocking** (`advisory_only: false`) | Block merge | 1 | ❌ Failed |
| **Advisory** (`advisory_only: true`) | Warn only | 0 | ⚠️ Warnings |

**Recommendation:** Start with advisory mode when adopting contract validation, then switch to blocking after stabilizing.

### PR Comments

The workflow posts a comment on each PR with validation results:

```markdown
## ✅ Contract Validation Passed

| Property | Value |
|----------|-------|
| **Mode** | Blocking |
| **Contracts Tag** | `stable` |
| **Violations** | 0 |
| **Warnings** | 0 |

### ✨ No issues found

All infrastructure contracts are satisfied.
```

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

**Note:** Bypass labels should only be used for urgent situations. All bypasses are logged for audit purposes.

---

## Related Documentation

- [PROC-007: Early Integration Bug Detection](https://github.com/teric-io/teric-planning/blob/main/PROC-007-early-integration-bug-detection.md)
- [INTEGRATION_CONTRACTS_GUIDELINES.md](https://github.com/teric-io/teric-meta/blob/main/guidelines/INTEGRATION_CONTRACTS_GUIDELINES.md)
- [@teric/contracts Package](https://github.com/teric-io/teric-contracts)

---

## Contributing

Changes to this repository affect all teric-io repositories. Please:

1. Test workflow changes in a fork first
2. Create a PR with clear description of changes
3. Get approval from platform team
4. Run validation tests locally before merging

### Local Testing

```bash
# Test the validation script locally
cd .github/scripts
npm install
node validate-contracts.js --config /path/to/infra.contracts.yaml --advisory
```

---

*Part of [PROC-007: Early Integration Bug Detection](https://github.com/teric-io/teric-planning/blob/main/PROC-007-early-integration-bug-detection.md)*
