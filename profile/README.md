# Teric.io

**Agentic development platform for building intelligent, self-improving software systems.**

## Overview

Teric is a research project exploring the intersection of AI agents and software development. We're building an end-to-end platform where AI agents:

- 📝 **Plan** features and architecture (teric-planning)
- 💻 **Implement** code changes (coding agents)
- 🔍 **Review** pull requests (app-code-review)
- 🧪 **Test** end-to-end scenarios (app-e2e-tester)
- 📊 **Coordinate** work items (mcp-work-coordinator)
- 🧠 **Learn** from patterns (mcp-knowledge-graph)

## Key Repositories

| Repository | Purpose |
|------------|---------|
| [teric-planning](https://github.com/teric-io/teric-planning) | Planning documents, architecture specs |
| [teric-meta](https://github.com/teric-io/teric-meta) | Guidelines, standards, agent configurations |
| [terraform-definitions](https://github.com/teric-io/terraform-definitions) | Infrastructure as code |
| [teric-orchestrator](https://github.com/teric-io/teric-orchestrator) | Agent execution orchestration |
| [app-code-review](https://github.com/teric-io/app-code-review) | Automated code review |
| [mcp-work-coordinator](https://github.com/teric-io/mcp-work-coordinator) | Work item management MCP server |
| [mcp-knowledge-graph](https://github.com/teric-io/mcp-knowledge-graph) | Knowledge storage MCP server |

## Integration Dependency Declarations

We use `infra.contracts.yaml` files to declare cross-repo infrastructure and
service dependencies:

```yaml
# infra.contracts.yaml
dynamodb:
  tables:
    - name: work-items
      operations:
        - GetItem
        - Query
```

The old PROC-007 central registry sync is retired; declarations remain useful
as repo-local source material for implementation and review.

[Read the integration contracts guidelines](https://github.com/teric-io/teric-meta/blob/main/guidelines/INTEGRATION_CONTRACTS_GUIDELINES.md)

## Contributing

This is primarily a research project. If you're interested in agentic development, reach out!

---

*Built with Claude + human oversight*
