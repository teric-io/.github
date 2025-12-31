#!/usr/bin/env node
/**
 * validate-contracts.js
 *
 * Validates repository code against declared infrastructure contracts.
 * Part of PROC-007: Early Integration Bug Detection System.
 *
 * Usage:
 *   node validate-contracts.js --config infra.contracts.yaml [--advisory]
 *
 * Output: JSON with validation results
 */

const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');

// Parse command line arguments
const args = process.argv.slice(2);
const configIndex = args.indexOf('--config');
const advisoryMode = args.includes('--advisory');
const configFile = configIndex !== -1 ? args[configIndex + 1] : 'infra.contracts.yaml';

/**
 * Known DynamoDB table name patterns from @teric/contracts
 * These are the canonical table names that should be used via contracts
 */
const KNOWN_TABLE_PATTERNS = [
  'teric-agentic-work-items',
  'teric-agentic-initiatives',
  'teric-agentic-code-reviews',
  'teric-agentic-knowledge-facts',
  'teric-agentic-e2e-test-runs',
  'teric-agentic-e2e-consecutive-passes',
  'teric-agentic-e2e-repository-registry',
  'teric-agentic-workflows',
  'teric-agentic-executions',
  'teric-agentic-metrics',
  'teric-agentic-contract-release-records',
  'teric-agentic-crr-consumption-tokens',
];

/**
 * Known Lambda function patterns
 */
const KNOWN_LAMBDA_PATTERNS = [
  'app-code-review',
  'app-e2e-tester',
  'app-planning-coordinator',
  'app-infra-promoter',
  'app-llm-advisor',
  'mcp-work-coordinator',
  'mcp-knowledge-graph',
  'teric-orchestrator',
];

/**
 * Known EventBridge source patterns
 */
const KNOWN_EVENT_SOURCES = [
  'teric.orchestrator',
  'teric.code-review',
  'teric.e2e-tester',
  'teric.planning',
  'teric.infra-promoter',
];

/**
 * Scan a file for hardcoded infrastructure references
 */
function scanFile(filePath, contracts) {
  const violations = [];
  const warnings = [];

  try {
    const content = fs.readFileSync(filePath, 'utf8');
    const lines = content.split('\n');

    lines.forEach((line, index) => {
      const lineNum = index + 1;

      // Skip comments
      if (line.trim().startsWith('//') || line.trim().startsWith('#') || line.trim().startsWith('*')) {
        return;
      }

      // Check for hardcoded DynamoDB table names
      KNOWN_TABLE_PATTERNS.forEach((tableName) => {
        // Look for string literals containing table names
        const regex = new RegExp(`['"\`]${tableName.replace(/-/g, '[-]?')}['"\`]`, 'gi');
        if (regex.test(line)) {
          // Check if this is in a contract definition or test file
          if (
            filePath.includes('/contracts/') ||
            filePath.includes('.test.') ||
            filePath.includes('.spec.') ||
            filePath.includes('__tests__')
          ) {
            return; // Skip contract definitions and test files
          }

          violations.push({
            type: 'hardcoded_table_name',
            severity: 'error',
            file: filePath,
            line: lineNum,
            message: `Hardcoded table name '${tableName}' should use @teric/contracts`,
            contract: `@teric/contracts/dynamodb/${tableName.replace('teric-agentic-', '')}`,
            snippet: line.trim().substring(0, 100),
          });
        }
      });

      // Check for hardcoded Lambda function names in invoke calls
      KNOWN_LAMBDA_PATTERNS.forEach((funcName) => {
        const invokePattern = new RegExp(`FunctionName['"\\s:]+['"\`]${funcName}['"\`]`, 'i');
        if (invokePattern.test(line)) {
          if (filePath.includes('/contracts/') || filePath.includes('.test.') || filePath.includes('.spec.')) {
            return;
          }

          violations.push({
            type: 'hardcoded_lambda_name',
            severity: 'error',
            file: filePath,
            line: lineNum,
            message: `Hardcoded Lambda name '${funcName}' should use @teric/contracts`,
            contract: `@teric/contracts/lambda/${funcName}`,
            snippet: line.trim().substring(0, 100),
          });
        }
      });

      // Check for hardcoded EventBridge sources
      KNOWN_EVENT_SOURCES.forEach((source) => {
        const sourcePattern = new RegExp(`Source['"\\s:]+['"\`]${source}['"\`]`, 'i');
        if (sourcePattern.test(line)) {
          if (filePath.includes('/contracts/') || filePath.includes('.test.') || filePath.includes('.spec.')) {
            return;
          }

          warnings.push({
            type: 'hardcoded_event_source',
            severity: 'warning',
            file: filePath,
            line: lineNum,
            message: `EventBridge source '${source}' should use @teric/contracts`,
            contract: `@teric/contracts/events/${source.replace('teric.', '')}`,
            snippet: line.trim().substring(0, 100),
          });
        }
      });
    });
  } catch (error) {
    warnings.push({
      type: 'scan_error',
      severity: 'warning',
      file: filePath,
      line: 0,
      message: `Could not scan file: ${error.message}`,
    });
  }

  return { violations, warnings };
}

/**
 * Get all source files in a directory recursively
 */
function getSourceFiles(dir, extensions = ['.ts', '.js', '.tsx', '.jsx']) {
  const files = [];

  if (!fs.existsSync(dir)) {
    return files;
  }

  const items = fs.readdirSync(dir, { withFileTypes: true });

  for (const item of items) {
    const fullPath = path.join(dir, item.name);

    // Skip common non-source directories
    if (
      item.isDirectory() &&
      !['node_modules', 'dist', 'build', '.git', 'coverage', '.next', '.cache'].includes(item.name)
    ) {
      files.push(...getSourceFiles(fullPath, extensions));
    } else if (item.isFile() && extensions.some((ext) => item.name.endsWith(ext))) {
      files.push(fullPath);
    }
  }

  return files;
}

/**
 * Validate declared contracts exist in @teric/contracts package
 */
function validateDeclaredContracts(contracts) {
  const warnings = [];

  // Check if @teric/contracts is available
  try {
    require.resolve('@teric/contracts');
  } catch {
    warnings.push({
      type: 'package_not_installed',
      severity: 'warning',
      message: '@teric/contracts package not installed - cannot validate contract declarations',
    });
    return warnings;
  }

  // Validate DynamoDB table declarations
  if (contracts.dynamodb?.tables) {
    for (const table of contracts.dynamodb.tables) {
      // Contract paths should be validated against actual exports
      // For now, just check the path format
      if (table.contract && !table.contract.startsWith('@teric/contracts/')) {
        warnings.push({
          type: 'invalid_contract_path',
          severity: 'warning',
          message: `Invalid contract path: ${table.contract}`,
          expected: '@teric/contracts/dynamodb/<table-name>',
        });
      }
    }
  }

  return warnings;
}

/**
 * Parse the infra.contracts.yaml file
 */
function parseContractsFile(configPath) {
  if (!fs.existsSync(configPath)) {
    return { error: `Config file not found: ${configPath}` };
  }

  try {
    const content = fs.readFileSync(configPath, 'utf8');
    return yaml.load(content);
  } catch (error) {
    return { error: `Failed to parse config: ${error.message}` };
  }
}

/**
 * Main validation function
 */
function validate() {
  const result = {
    passed: true,
    violations: [],
    warnings: [],
    summary: {
      total: 0,
      errors: 0,
      warnings: 0,
      filesScanned: 0,
    },
    metadata: {
      configFile,
      advisoryMode,
      timestamp: new Date().toISOString(),
      version: '1.0.0',
    },
  };

  // Parse contracts file
  const contracts = parseContractsFile(configFile);

  if (contracts.error) {
    result.warnings.push({
      type: 'config_error',
      severity: 'warning',
      message: contracts.error,
    });
    // Output result and exit
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  // Validate declared contracts
  const declaredWarnings = validateDeclaredContracts(contracts);
  result.warnings.push(...declaredWarnings);

  // Scan source files for hardcoded references
  const srcDirs = ['src', 'lib', 'handlers', 'services', 'components'];
  const sourceFiles = [];

  for (const dir of srcDirs) {
    sourceFiles.push(...getSourceFiles(dir));
  }

  // Also check root level TypeScript/JavaScript files
  if (fs.existsSync('.')) {
    const rootFiles = fs.readdirSync('.').filter((f) => {
      return (f.endsWith('.ts') || f.endsWith('.js')) && !f.includes('.config') && !f.includes('.test');
    });
    sourceFiles.push(...rootFiles);
  }

  result.summary.filesScanned = sourceFiles.length;

  // Scan each file
  for (const filePath of sourceFiles) {
    const { violations, warnings } = scanFile(filePath, contracts);
    result.violations.push(...violations);
    result.warnings.push(...warnings);
  }

  // Calculate summary
  result.summary.errors = result.violations.length;
  result.summary.warnings = result.warnings.length;
  result.summary.total = result.summary.errors + result.summary.warnings;

  // Determine pass/fail
  if (result.violations.length > 0) {
    result.passed = advisoryMode; // In advisory mode, violations don't cause failure
  }

  // Output result
  console.log(JSON.stringify(result, null, 2));

  // Exit with appropriate code
  if (!advisoryMode && result.violations.length > 0) {
    process.exit(1);
  }
}

// Run validation
validate();
