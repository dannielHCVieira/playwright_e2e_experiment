#!/usr/bin/env node
/**
 * Extracts individual Playwright tests (test/test.only/test.skip) from a spec file.
 * Outputs JSON array with metadata for each test.
 */

const fs = require('fs');
const path = require('path');
const ts = require('typescript');

function usageAndExit(message) {
  console.error(message);
  console.error('Usage: node extract_playwright_tests.js <path-to-spec.ts>');
  process.exit(1);
}

const specPath = process.argv[2];
if (!specPath) {
  usageAndExit('Missing spec path argument.');
}

const resolvedPath = path.resolve(specPath);
if (!fs.existsSync(resolvedPath)) {
  usageAndExit(`Spec file not found: ${resolvedPath}`);
}

const sourceText = fs.readFileSync(resolvedPath, 'utf8');
const sourceFile = ts.createSourceFile(
  resolvedPath,
  sourceText,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX
);

const tests = [];
let counter = 0;

function isTestNamespace(node) {
  return ts.isIdentifier(node) && node.text === 'test';
}

function getCallKind(callExpr) {
  const expr = callExpr.expression;
  if (isTestNamespace(expr)) {
    return 'test';
  }
  if (ts.isPropertyAccessExpression(expr) && isTestNamespace(expr.expression)) {
    const name = expr.name.text;
    if (name === 'describe') return 'describe';
    return 'test';
  }
  return null;
}

function getStringValue(node) {
  if (!node) return null;
  if (ts.isStringLiteralLike(node)) {
    return node.text;
  }
  if (ts.isNoSubstitutionTemplateLiteral(node)) {
    return node.text;
  }
  return null;
}

function recordTest(callExpr, contextStack) {
  const args = callExpr.arguments;
  if (args.length < 2) return;

  const titleNode = args[0];
  const fnNode = args[1];
  const testName = getStringValue(titleNode);
  if (!testName) return;
  if (
    !ts.isFunctionLike(fnNode) &&
    !(ts.isArrowFunction(fnNode) || ts.isFunctionExpression(fnNode))
  ) {
    return;
  }

  const body = fnNode.body ? fnNode.body.getText(sourceFile) : fnNode.getText(sourceFile);
  const fullNameParts = [...contextStack, testName];
  counter += 1;

  const { line } = sourceFile.getLineAndCharacterOfPosition(callExpr.getStart());

  tests.push({
    name: testName,
    fullName: fullNameParts.join(' > '),
    line: line + 1,
    index: counter,
    code: body.trim(),
  });
}

function traverse(node, contextStack = []) {
  if (ts.isCallExpression(node)) {
    const kind = getCallKind(node);
    if (kind === 'describe') {
      const title = getStringValue(node.arguments[0]);
      const fnNode = node.arguments[1];
      if (title && fnNode && fnNode.body) {
        const newContext = [...contextStack, title];
        if (ts.isBlock(fnNode.body)) {
          fnNode.body.statements.forEach((stmt) => traverse(stmt, newContext));
          return;
        }
      }
    } else if (kind === 'test') {
      recordTest(node, contextStack);
      return;
    }
  }
  ts.forEachChild(node, (child) => traverse(child, contextStack));
}

traverse(sourceFile, []);

console.log(JSON.stringify(tests));

