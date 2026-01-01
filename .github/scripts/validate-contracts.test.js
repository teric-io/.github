/**
 * Unit tests for validate-contracts.js
 *
 * Tests the contract validation logic for detecting hardcoded infrastructure references.
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Path to the validation script
const SCRIPT_PATH = path.join(__dirname, 'validate-contracts.js');

// Test fixtures directory
const FIXTURES_DIR = path.join(__dirname, '__fixtures__');

describe('validate-contracts.js', () => {
  beforeAll(() => {
    // Create fixtures directory
    if (!fs.existsSync(FIXTURES_DIR)) {
      fs.mkdirSync(FIXTURES_DIR, { recursive: true });
    }
  });

  afterAll(() => {
    // Clean up fixtures directory
    if (fs.existsSync(FIXTURES_DIR)) {
      fs.rmSync(FIXTURES_DIR, { recursive: true });
    }
  });

  describe('when no infra.contracts.yaml exists', () => {
    test('should output passed=true with config error warning', () => {
      const result = runValidation('nonexistent.yaml', { advisory: true });

      expect(result.passed).toBe(true);
      expect(result.warnings).toBeDefined();
      expect(result.warnings.some((w) => w.type === 'config_error')).toBe(true);
    });
  });

  describe('when infra.contracts.yaml exists', () => {
    beforeEach(() => {
      // Create a minimal contracts file
      const contractsContent = `
contract_version: "1.0"
repository: "teric-io/test-repo"
updated_at: "2025-01-01"
contracts:
  source: "@teric-io/contracts"
  tag: stable
dynamodb:
  tables:
    - name: work-items
      contract: "@teric-io/contracts/dynamodb/work-items"
`;
      fs.writeFileSync(path.join(FIXTURES_DIR, 'infra.contracts.yaml'), contractsContent);
    });

    test('should pass with no violations when code is clean', () => {
      // Create a clean source file
      const srcDir = path.join(FIXTURES_DIR, 'src');
      if (!fs.existsSync(srcDir)) {
        fs.mkdirSync(srcDir);
      }

      fs.writeFileSync(
        path.join(srcDir, 'handler.ts'),
        `
import { WorkItemsTable } from '@teric-io/contracts/dynamodb';

export async function handler() {
  const tableName = WorkItemsTable.name;
  return { statusCode: 200 };
}
`
      );

      const result = runValidationInDir(FIXTURES_DIR, 'infra.contracts.yaml');

      expect(result.passed).toBe(true);
      expect(result.violations).toHaveLength(0);
    });

    test('should detect hardcoded DynamoDB table names', () => {
      // Create a source file with hardcoded table name
      const srcDir = path.join(FIXTURES_DIR, 'src');
      if (!fs.existsSync(srcDir)) {
        fs.mkdirSync(srcDir);
      }

      fs.writeFileSync(
        path.join(srcDir, 'handler.ts'),
        `
export async function handler() {
  const tableName = 'teric-agentic-work-items';
  return { statusCode: 200 };
}
`
      );

      const result = runValidationInDir(FIXTURES_DIR, 'infra.contracts.yaml', { advisory: true });

      expect(result.passed).toBe(true); // Advisory mode
      expect(result.violations.length).toBeGreaterThan(0);
      expect(result.violations[0].type).toBe('hardcoded_table_name');
      expect(result.violations[0].file).toContain('handler.ts');
    });

    test('should detect hardcoded Lambda function names', () => {
      const srcDir = path.join(FIXTURES_DIR, 'src');
      if (!fs.existsSync(srcDir)) {
        fs.mkdirSync(srcDir);
      }

      fs.writeFileSync(
        path.join(srcDir, 'invoker.ts'),
        `
export async function invokeReview() {
  const params = {
    FunctionName: 'app-code-review',
    Payload: JSON.stringify({})
  };
  return params;
}
`
      );

      const result = runValidationInDir(FIXTURES_DIR, 'infra.contracts.yaml', { advisory: true });

      expect(result.violations.some((v) => v.type === 'hardcoded_lambda_name')).toBe(true);
    });

    test('should skip test files', () => {
      const srcDir = path.join(FIXTURES_DIR, 'src');
      if (!fs.existsSync(srcDir)) {
        fs.mkdirSync(srcDir);
      }

      // Clean up any previous files
      fs.readdirSync(srcDir).forEach((f) => fs.unlinkSync(path.join(srcDir, f)));

      // Create a test file with hardcoded values (should be skipped)
      fs.writeFileSync(
        path.join(srcDir, 'handler.test.ts'),
        `
describe('handler', () => {
  test('should work', () => {
    const tableName = 'teric-agentic-work-items';
    expect(tableName).toBe('teric-agentic-work-items');
  });
});
`
      );

      const result = runValidationInDir(FIXTURES_DIR, 'infra.contracts.yaml');

      // Test files should be skipped
      expect(result.violations).toHaveLength(0);
    });

    test('should report EventBridge sources as warnings', () => {
      const srcDir = path.join(FIXTURES_DIR, 'src');
      if (!fs.existsSync(srcDir)) {
        fs.mkdirSync(srcDir);
      }

      // Clean up any previous files
      fs.readdirSync(srcDir).forEach((f) => fs.unlinkSync(path.join(srcDir, f)));

      fs.writeFileSync(
        path.join(srcDir, 'events.ts'),
        `
export const event = {
  Source: 'teric.orchestrator',
  DetailType: 'WorkItemCompleted'
};
`
      );

      const result = runValidationInDir(FIXTURES_DIR, 'infra.contracts.yaml', { advisory: true });

      expect(result.warnings.some((w) => w.type === 'hardcoded_event_source')).toBe(true);
    });
  });

  describe('advisory mode', () => {
    beforeEach(() => {
      const contractsContent = `
contract_version: "1.0"
repository: "teric-io/test-repo"
`;
      fs.writeFileSync(path.join(FIXTURES_DIR, 'infra.contracts.yaml'), contractsContent);

      const srcDir = path.join(FIXTURES_DIR, 'src');
      if (!fs.existsSync(srcDir)) {
        fs.mkdirSync(srcDir);
      }

      fs.writeFileSync(
        path.join(srcDir, 'handler.ts'),
        `const table = 'teric-agentic-work-items';`
      );
    });

    test('should set passed=true even with violations in advisory mode', () => {
      const result = runValidationInDir(FIXTURES_DIR, 'infra.contracts.yaml', { advisory: true });

      expect(result.passed).toBe(true);
      expect(result.violations.length).toBeGreaterThan(0);
    });

    test('should set passed=false with violations in blocking mode', () => {
      const result = runValidationInDir(FIXTURES_DIR, 'infra.contracts.yaml', { advisory: false });

      expect(result.passed).toBe(false);
      expect(result.violations.length).toBeGreaterThan(0);
    });
  });

  describe('output format', () => {
    beforeEach(() => {
      const contractsContent = `
contract_version: "1.0"
repository: "teric-io/test-repo"
`;
      fs.writeFileSync(path.join(FIXTURES_DIR, 'infra.contracts.yaml'), contractsContent);
    });

    test('should include required metadata fields', () => {
      const result = runValidationInDir(FIXTURES_DIR, 'infra.contracts.yaml', { advisory: true });

      expect(result).toHaveProperty('passed');
      expect(result).toHaveProperty('violations');
      expect(result).toHaveProperty('warnings');
      expect(result).toHaveProperty('summary');
      expect(result).toHaveProperty('metadata');

      expect(result.metadata).toHaveProperty('configFile');
      expect(result.metadata).toHaveProperty('advisoryMode');
      expect(result.metadata).toHaveProperty('timestamp');
      expect(result.metadata).toHaveProperty('version');
    });

    test('should include summary counts', () => {
      const result = runValidationInDir(FIXTURES_DIR, 'infra.contracts.yaml', { advisory: true });

      expect(result.summary).toHaveProperty('total');
      expect(result.summary).toHaveProperty('errors');
      expect(result.summary).toHaveProperty('warnings');
      expect(result.summary).toHaveProperty('filesScanned');
    });
  });
});

/**
 * Run the validation script and parse the JSON output
 */
function runValidation(configFile, options = {}) {
  const args = ['--config', configFile];
  if (options.advisory) {
    args.push('--advisory');
  }

  try {
    const output = execSync(`node ${SCRIPT_PATH} ${args.join(' ')}`, {
      encoding: 'utf8',
      cwd: FIXTURES_DIR,
    });
    return JSON.parse(output);
  } catch (error) {
    // Script may exit with non-zero in blocking mode
    if (error.stdout) {
      return JSON.parse(error.stdout);
    }
    throw error;
  }
}

/**
 * Run validation in a specific directory
 */
function runValidationInDir(dir, configFile, options = {}) {
  const args = ['--config', configFile];
  if (options.advisory) {
    args.push('--advisory');
  }

  try {
    const output = execSync(`node ${SCRIPT_PATH} ${args.join(' ')}`, {
      encoding: 'utf8',
      cwd: dir,
    });
    return JSON.parse(output);
  } catch (error) {
    // Script may exit with non-zero in blocking mode
    if (error.stdout) {
      return JSON.parse(error.stdout);
    }
    throw error;
  }
}
