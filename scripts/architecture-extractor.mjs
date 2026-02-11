#!/usr/bin/env node
// PROC-008 Phase 4: Architecture Extractor Agent
//
// Uses Claude Haiku to analyze a repository and generate a component.yaml
// following the teric-meta/architecture/schema/component-schema.yaml schema.
//
// Environment variables:
//   ANTHROPIC_API_KEY        - Claude API token (from claude-token-broker Lambda)
//   REPO_NAME                - Repository name (e.g., "app-code-review")
//   SCHEMA_PATH              - Path to component-schema.yaml
//   EXISTING_COMPONENT_PATH  - Path to existing component file (may not exist)
//   SOURCE_REPO_PATH         - Path to the source repository root
//
// Output: component-output.yaml in current working directory

import Anthropic from "@anthropic-ai/sdk";
import * as fs from "node:fs";
import * as path from "node:path";
import yaml from "js-yaml";

const REPO_NAME = process.env.REPO_NAME;
const SCHEMA_PATH = process.env.SCHEMA_PATH;
const EXISTING_COMPONENT_PATH = process.env.EXISTING_COMPONENT_PATH;
const SOURCE_REPO_PATH = process.env.SOURCE_REPO_PATH || ".";
const CLAUDE_MODEL = process.env.CLAUDE_MODEL || "claude-haiku-4-5-20251001";

if (!REPO_NAME) {
  console.error("REPO_NAME is required");
  process.exit(1);
}
if (!SCHEMA_PATH) {
  console.error("SCHEMA_PATH is required");
  process.exit(1);
}
if (!fs.existsSync(SOURCE_REPO_PATH)) {
  console.error(`SOURCE_REPO_PATH does not exist: ${SOURCE_REPO_PATH}`);
  process.exit(1);
}

// Files to read from the source repo, in priority order
const FILE_PRIORITIES = [
  // Priority 1: Always read
  { path: "package.json", required: false },
  { path: "CLAUDE.md", required: false },
  { path: "README.md", required: false, maxLines: 100 },
  { path: "tsconfig.json", required: false },
  { path: "infra.contracts.yaml", required: false },
  { path: ".env.example", required: false },
  // Priority 2: Entry points
  { path: "src/handler.ts", required: false },
  { path: "src/index.ts", required: false },
];

// Directories to scan for handler/tool files
const SCAN_DIRS = ["src/handlers", "src/tools", "src/commands"];

// Patterns to grep for in src/ (infrastructure and service references)
const GREP_PATTERNS = [
  { pattern: "DynamoDBClient|DynamoDBDocumentClient", label: "DynamoDB usage" },
  { pattern: "SSMClient|getParameter", label: "SSM usage" },
  { pattern: "LambdaClient|lambda\\.invoke", label: "Lambda invocation" },
  { pattern: "S3Client|PutObjectCommand|GetObjectCommand", label: "S3 usage" },
  {
    pattern: "EventBridgeClient|PutEventsCommand|putEvents",
    label: "EventBridge usage",
  },
  { pattern: "SFNClient|StartExecutionCommand", label: "Step Functions usage" },
  { pattern: "SQSClient|SendMessageCommand", label: "SQS usage" },
  { pattern: "mcp__", label: "MCP tool calls" },
  {
    pattern: "process\\.env\\.",
    label: "Environment variables",
    maxMatches: 30, // Limit to keep prompt size manageable
  },
  {
    pattern: "ENFORCE_|USE_|ENABLE_|getFeatureFlag|isEnabled",
    label: "Feature flags",
  },
];

function readFileIfExists(filePath, maxLines) {
  const fullPath = path.join(SOURCE_REPO_PATH, filePath);
  if (!fs.existsSync(fullPath)) return null;

  let content = fs.readFileSync(fullPath, "utf-8");
  if (maxLines) {
    content = content.split("\n").slice(0, maxLines).join("\n");
  }
  // Truncate very large files to keep prompt size manageable
  if (content.length > 8000) {
    console.warn(`[architecture-extractor] Truncating ${filePath} (${content.length} chars > 8000)`);
    content = content.slice(0, 8000) + "\n... (truncated)";
  }
  return content;
}

function listDirFiles(dirPath) {
  const fullPath = path.join(SOURCE_REPO_PATH, dirPath);
  if (!fs.existsSync(fullPath) || !fs.statSync(fullPath).isDirectory())
    return [];

  return fs
    .readdirSync(fullPath)
    .filter((f) => f.endsWith(".ts") || f.endsWith(".js"))
    .map((f) => path.join(dirPath, f));
}

function grepInSrc(pattern, maxMatches = 10) {
  const srcPath = path.join(SOURCE_REPO_PATH, "src");
  if (!fs.existsSync(srcPath)) return [];

  const results = [];

  function walk(dir) {
    if (results.length >= maxMatches) return;
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (results.length >= maxMatches) return;
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory() && entry.name !== "node_modules") {
        walk(fullPath);
      } else if (
        entry.isFile() &&
        (entry.name.endsWith(".ts") || entry.name.endsWith(".js"))
      ) {
        const content = fs.readFileSync(fullPath, "utf-8");
        const regex = new RegExp(pattern, "g");
        const lines = content.split("\n");
        for (let i = 0; i < lines.length; i++) {
          if (regex.test(lines[i])) {
            const relPath = path.relative(SOURCE_REPO_PATH, fullPath);
            results.push(`${relPath}:${i + 1}: ${lines[i].trim()}`);
            if (results.length >= maxMatches) return;
          }
        }
      }
    }
  }

  walk(srcPath);
  return results;
}

async function main() {
  console.log(`[architecture-extractor] Analyzing ${REPO_NAME}...`);

  // 1. Read schema
  const schemaContent = fs.existsSync(SCHEMA_PATH)
    ? fs.readFileSync(SCHEMA_PATH, "utf-8")
    : "Schema file not found - generate based on the examples below.";

  // 2. Read existing component (for merge mode)
  let existingComponent = null;
  if (
    EXISTING_COMPONENT_PATH &&
    fs.existsSync(EXISTING_COMPONENT_PATH)
  ) {
    existingComponent = fs.readFileSync(EXISTING_COMPONENT_PATH, "utf-8");
    console.log(`[architecture-extractor] Update mode: existing component found`);
  } else {
    console.log(`[architecture-extractor] Bootstrap mode: no existing component`);
  }

  // 3. Gather files
  const fileContents = [];
  for (const file of FILE_PRIORITIES) {
    const content = readFileIfExists(file.path, file.maxLines);
    if (content) {
      fileContents.push(`### ${file.path}\n\`\`\`\n${content}\n\`\`\``);
    }
  }

  // 4. Scan handler/tool directories
  for (const dir of SCAN_DIRS) {
    const files = listDirFiles(dir);
    for (const f of files) {
      const content = readFileIfExists(f, 80);
      if (content) {
        fileContents.push(
          `### ${f} (first 80 lines)\n\`\`\`\n${content}\n\`\`\``
        );
      }
    }
  }

  // 5. Grep for infrastructure patterns
  const grepResults = [];
  for (const { pattern, label, maxMatches } of GREP_PATTERNS) {
    const matches = grepInSrc(pattern, maxMatches);
    if (matches.length > 0) {
      grepResults.push(`#### ${label}\n${matches.join("\n")}`);
    }
  }

  // 6. Build prompt
  const prompt = `You are an architecture documentation extractor. Analyze this repository and generate a component.yaml file.

## Repository: ${REPO_NAME}

## Component Schema (follow this EXACTLY)
\`\`\`yaml
${schemaContent}
\`\`\`

${
  existingComponent
    ? `## Existing Component (UPDATE mode - preserve human-curated fields like responsibilities, descriptions, and purposes; refresh auto-extracted fields like dependencies, entrypoints, configuration)
\`\`\`yaml
${existingComponent}
\`\`\``
    : "## No existing component (BOOTSTRAP mode - generate all fields from scratch)"
}

## Source Files
${fileContents.join("\n\n")}

## Infrastructure & Service References (grep results from src/)
${grepResults.join("\n\n") || "No matches found."}

## Instructions
1. Generate a complete component.yaml following the schema exactly.
2. ${existingComponent ? "Preserve human-written responsibilities and descriptions. Refresh dependencies, entrypoints, and configuration from code." : "Generate all fields from the code analysis."}
3. Be specific about infrastructure dependencies (include table names, SSM paths found in code).
4. List ALL service dependencies found in grep results.
5. Set updated_by to "architecture-extractor" and updated_at to the current date.
6. Only include fields you have evidence for - do not hallucinate dependencies.

Output ONLY the YAML content, starting with "component:" - no markdown fences, no explanation.`;

  // 7. Call Claude Haiku
  const client = new Anthropic();
  console.log(`[architecture-extractor] Calling Claude Haiku...`);

  const response = await client.messages.create({
    model: CLAUDE_MODEL,
    max_tokens: 4096,
    messages: [{ role: "user", content: prompt }],
  });

  let yamlOutput = response.content[0].text.trim();

  // Strip markdown fences if present
  if (yamlOutput.startsWith("```")) {
    yamlOutput = yamlOutput
      .replace(/^```(?:yaml)?\n?/, "")
      .replace(/\n?```$/, "");
  }

  // 8. Validate YAML is parseable
  try {
    const parsed = yaml.load(yamlOutput);
    if (!parsed || !parsed.component) {
      throw new Error("Missing top-level 'component' key");
    }
    if (!parsed.component.id || !parsed.component.name || !parsed.component.type) {
      throw new Error("Missing required fields: id, name, or type");
    }
    console.log(
      `[architecture-extractor] Valid YAML generated for ${parsed.component.id}`
    );
  } catch (err) {
    console.error(
      `[architecture-extractor] YAML validation failed: ${err.message}`
    );
    console.error(`[architecture-extractor] Raw output:\n${yamlOutput}`);
    process.exit(1);
  }

  // 9. Add header comment and write output
  const header = `# Component: ${REPO_NAME}
# Generated by architecture-extractor (PROC-008 Phase 4)
# Generated at: ${new Date().toISOString()}
# Schema: component-schema.yaml v1.0.0
#
# Human-curated fields (responsibilities, descriptions) are preserved on update.
# Auto-extracted fields (dependencies, entrypoints) are refreshed from code.

`;

  fs.writeFileSync("component-output.yaml", header + yamlOutput + "\n");
  console.log(
    `[architecture-extractor] Output written to component-output.yaml`
  );
}

main().catch((err) => {
  console.error(`[architecture-extractor] Fatal error: ${err.message}`);
  process.exit(1);
});
