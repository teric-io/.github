---
name: Agent Blocked
about: Agent requires human approval or assistance
title: '[AGENT-BLOCKED] '
labels: ['agent-blocked', 'needs-approval']
assignees: ''
---

## Agent Execution Context

- **Execution ID**:
- **Work Item ID**:
- **Repository**:
- **Branch**:
- **Planning Doc**:

## Blocking Reason

Select the reason for blocking:

- [ ] **Cost Approval** - Operation exceeds $7.50 threshold
- [ ] **Infrastructure Approval** - Terraform changes require human approval
- [ ] **Security Sensitive** - Credentials, IAM, or security-related changes
- [ ] **Ambiguous Requirements** - Multiple valid approaches, need clarification
- [ ] **Test Failures** - Persistent test failures after multiple attempts
- [ ] **Merge Conflicts** - Cannot auto-resolve merge conflicts
- [ ] **Design Decision** - Architecture decision with unclear trade-offs
- [ ] **Other** - See description

## Description

Detailed explanation of why the agent is blocked.

## Proposed Action

What action does the agent suggest? What are the options?

## Impact of Delay

What happens if this isn't resolved? Are there dependent tasks waiting?

---

### For Human Reviewer

To unblock this agent:

1. Review the blocking reason and proposed action
2. Add a comment with your decision
3. Remove the `agent-blocked` label OR add `approved` label
4. The agent will resume automatically (or notify manually)

**Approval Commands (in comment):**
- `/approve` - Approve the proposed action
- `/approve-with-conditions <conditions>` - Approve with specific conditions
- `/reject <reason>` - Reject and provide alternative direction
- `/escalate` - Escalate to Tim for review
