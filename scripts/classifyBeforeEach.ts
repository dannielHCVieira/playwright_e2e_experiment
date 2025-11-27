import fs from 'fs';
import path from 'path';
import ts from 'typescript';

type Category = 'script' | 'user' | 'mixed' | 'neutral';

interface StatementInfo {
  node: ts.Node;
  text: string;
  startLine: number;
  scriptCalls: string[];
  userCalls: string[];
  isScript: boolean;
  isUser: boolean;
}

interface UserInstruction {
  line: number;
  source: string;
  summary: string;
}

interface BeforeEachEntry {
  file: string;
  line: number;
  category: Category;
  scriptCalls: string[];
  userCalls: string[];
  scriptSnippet: string;
  userInstructions: UserInstruction[];
}

interface ClassificationOutput {
  generatedAt: string;
  entries: BeforeEachEntry[];
}

const SCRIPT_APIS = new Set([
  'addInitScript',
  'exposeFunction',
  'route',
  'unroute',
  'context',
  'setExtraHTTPHeaders',
  'setDefaultNavigationTimeout',
  'setDefaultTimeout',
  'setViewportSize',
  'waitForEvent',
  'evaluateOnNewDocument',
]);

const SCRIPT_API_PATHS = new Set([
  'keyboard.insertText',
]);

const USER_APIS = new Set([
  'goto',
  'click',
  'dblclick',
  'fill',
  'press',
  'type',
  'check',
  'uncheck',
  'hover',
  'tap',
  'focus',
  'locator',
  'frameLocator',
  'getByRole',
  'getByLabel',
  'getByText',
  'getByAltText',
  'getByPlaceholder',
  'getByTestId',
  'getByTitle',
  'selectOption',
  'setInputFiles',
  'dragTo',
  'keyboard',
  'mouse',
]);

const USER_API_PREFIXES = ['getBy'];

const EXCLUDED_DIRS = new Set(['node_modules', '.git', 'dist']);

const OUTPUT_JSON = path.join('examples', 'beforeeach-classification.json');
const OUTPUT_PROMPTS = path.join('examples', 'beforeeach-prompts.md');

function main() {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const examplesRoot = path.join(repoRoot, 'examples');
  if (!fs.existsSync(examplesRoot)) {
    console.error('Examples directory not found. Nothing to classify.');
    process.exit(1);
  }

  const files = collectTestFiles(examplesRoot);
  const entries = files.flatMap(file => analyzeFile(file, repoRoot));

  entries.sort((a, b) => {
    if (a.file === b.file) {
      return a.line - b.line;
    }
    return a.file.localeCompare(b.file);
  });

  const output: ClassificationOutput = {
    generatedAt: new Date().toISOString(),
    entries,
  };

  const jsonPath = path.join(repoRoot, OUTPUT_JSON);
  const promptPath = path.join(repoRoot, OUTPUT_PROMPTS);

  fs.writeFileSync(jsonPath, JSON.stringify(output, null, 2));
  fs.writeFileSync(promptPath, buildPromptMarkdown(output));

  console.log(`Processed ${files.length} files, generated ${entries.length} beforeEach classifications.`);
  console.log(`JSON: ${path.relative(repoRoot, jsonPath)}`);
  console.log(`Prompts: ${path.relative(repoRoot, promptPath)}`);
}

function collectTestFiles(targetDir: string): string[] {
  const results: string[] = [];
  const entries = fs.readdirSync(targetDir, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (EXCLUDED_DIRS.has(entry.name)) {
        continue;
      }
      results.push(...collectTestFiles(path.join(targetDir, entry.name)));
    } else if (entry.isFile()) {
      const filePath = path.join(targetDir, entry.name);
      if (isTestFile(filePath)) {
        results.push(filePath);
      }
    }
  }
  return results;
}

function isTestFile(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, '/');
  const inTests = normalized.includes('/tests/');
  const allowedExtensions = new Set(['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']);
  const ext = path.extname(filePath).toLowerCase();
  return inTests && allowedExtensions.has(ext);
}

function analyzeFile(filePath: string, repoRoot: string): BeforeEachEntry[] {
  const content = fs.readFileSync(filePath, 'utf-8');
  const sourceFile = ts.createSourceFile(
    filePath,
    content,
    ts.ScriptTarget.Latest,
    true,
    inferScriptKind(filePath),
  );

  const entries: BeforeEachEntry[] = [];

  function visit(node: ts.Node) {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
      const propertyName = node.expression.name.getText(sourceFile);
      if (propertyName === 'beforeEach' && isTestIdentifier(node.expression.expression)) {
        const callback = node.arguments[0];
        if (callback && (ts.isArrowFunction(callback) || ts.isFunctionExpression(callback))) {
          const entry = processBeforeEach(callback, node, sourceFile, repoRoot);
          if (entry) {
            entries.push(entry);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return entries;
}

function inferScriptKind(filePath: string): ts.ScriptKind {
  const ext = path.extname(filePath).toLowerCase();
  switch (ext) {
    case '.ts':
      return ts.ScriptKind.TS;
    case '.tsx':
      return ts.ScriptKind.TSX;
    case '.jsx':
      return ts.ScriptKind.JSX;
    default:
      return ts.ScriptKind.JS;
  }
}

function isTestIdentifier(expr: ts.Expression): boolean {
  return ts.isIdentifier(expr) && expr.text === 'test';
}

function processBeforeEach(
  callback: ts.ArrowFunction | ts.FunctionExpression,
  callExpression: ts.CallExpression,
  sourceFile: ts.SourceFile,
  repoRoot: string,
): BeforeEachEntry | null {
  if (!callback.body) {
    return null;
  }

  const statements = getStatementsFromBody(callback.body);
  if (!statements.length) {
    return null;
  }

  const pageAliases = collectPageAliases(callback);
  if (!pageAliases.size) {
    pageAliases.add('page');
  }

  const statementInfo = statements.map(statement =>
    summarizeStatement(statement, sourceFile, pageAliases),
  );

  const hasScript = statementInfo.some(info => info.isScript);
  const hasUser = statementInfo.some(info => info.isUser);

  const category: Category = hasScript && hasUser
    ? 'mixed'
    : hasScript
      ? 'script'
      : hasUser
        ? 'user'
        : 'neutral';

  const scriptCalls = uniqueValues(statementInfo.flatMap(info => info.scriptCalls));
  const userCalls = uniqueValues(statementInfo.flatMap(info => info.userCalls));

  const scriptSnippet = hasScript
    ? buildScriptSnippet(statementInfo)
    : '';

  const userInstructions = hasUser
    ? buildUserInstructions(statementInfo)
    : [];

  const location = sourceFile.getLineAndCharacterOfPosition(callExpression.getStart(sourceFile));
  const relativeFile = path.relative(repoRoot, sourceFile.fileName).replace(/\\/g, '/');

  return {
    file: relativeFile,
    line: location.line + 1,
    category,
    scriptCalls,
    userCalls,
    scriptSnippet,
    userInstructions,
  };
}

function getStatementsFromBody(body: ts.ConciseBody): ts.Node[] {
  if (ts.isBlock(body)) {
    return Array.from(body.statements);
  }
  return [body];
}

function collectPageAliases(callback: ts.ArrowFunction | ts.FunctionExpression): Set<string> {
  const aliases = new Set<string>();
  for (const parameter of callback.parameters) {
    extractPageNames(parameter.name, aliases);
  }
  return aliases;
}

function extractPageNames(bindingName: ts.BindingName, aliases: Set<string>) {
  if (ts.isObjectBindingPattern(bindingName)) {
    for (const element of bindingName.elements) {
      if (ts.isIdentifier(element.name)) {
        const propertyName = element.propertyName && ts.isIdentifier(element.propertyName)
          ? element.propertyName.text
          : element.name.text;
        if (propertyName === 'page') {
          aliases.add(element.name.text);
        }
      }
    }
  } else if (ts.isIdentifier(bindingName) && bindingName.text === 'page') {
    aliases.add(bindingName.text);
  }
}

function summarizeStatement(
  node: ts.Node,
  sourceFile: ts.SourceFile,
  pageAliases: Set<string>,
): StatementInfo {
  const scriptCalls = new Set<string>();
  const userCalls = new Set<string>();

  function visit(inner: ts.Node) {
    if (ts.isCallExpression(inner)) {
      const classification = classifyPageCall(inner, pageAliases);
      if (classification) {
        if (classification.kind === 'script') {
          scriptCalls.add(classification.name);
        } else if (classification.kind === 'user') {
          userCalls.add(classification.name);
        }
      }
    }
    ts.forEachChild(inner, visit);
  }

  visit(node);

  const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
  const text = node.getText(sourceFile).trim();

  return {
    node,
    text,
    startLine: start.line + 1,
    scriptCalls: Array.from(scriptCalls),
    userCalls: Array.from(userCalls),
    isScript: scriptCalls.size > 0,
    isUser: userCalls.size > 0,
  };
}

function classifyPageCall(
  call: ts.CallExpression,
  pageAliases: Set<string>,
): { kind: 'script' | 'user'; name: string } | null {
  if (!ts.isPropertyAccessExpression(call.expression)) {
    return null;
  }
  const propertyInfo = extractPropertyPath(call.expression);
  if (!propertyInfo || !propertyInfo.baseIdentifier || !pageAliases.has(propertyInfo.baseIdentifier)) {
    return null;
  }
  const firstProp = propertyInfo.propertyPath[0];
  const fullPath = propertyInfo.propertyPath.join('.');

  if (SCRIPT_APIS.has(firstProp) || SCRIPT_API_PATHS.has(fullPath)) {
    return { kind: 'script', name: fullPath };
  }

  if (isUserApi(firstProp, fullPath)) {
    return { kind: 'user', name: fullPath };
  }

  return null;
}

function extractPropertyPath(expression: ts.PropertyAccessExpression): { baseIdentifier: string | null; propertyPath: string[] } | null {
  const segments: string[] = [];
  let current: ts.Expression = expression;
  while (ts.isPropertyAccessExpression(current)) {
    segments.push(current.name.text);
    current = current.expression;
  }
  if (!ts.isIdentifier(current)) {
    return null;
  }
  segments.reverse();
  return {
    baseIdentifier: current.text,
    propertyPath: segments,
  };
}

function isUserApi(firstProperty: string, fullPath: string): boolean {
  if (USER_APIS.has(firstProperty)) {
    return true;
  }
  if (USER_API_PREFIXES.some(prefix => firstProperty.startsWith(prefix))) {
    return true;
  }
  if (fullPath === 'keyboard.press' || fullPath === 'keyboard.type') {
    return true;
  }
  return false;
}

function buildScriptSnippet(statements: StatementInfo[]): string {
  const parts = statements
    .filter(info => info.isScript || (!info.isScript && !info.isUser))
    .map(info => info.text.trim())
    .filter(Boolean);
  return parts.join('\n').trim();
}

function buildUserInstructions(statements: StatementInfo[]): UserInstruction[] {
  const instructions: UserInstruction[] = [];
  statements.forEach(info => {
    if (!info.isUser) {
      return;
    }
    const hasNonGotoCall = info.userCalls.some(call => call !== 'goto');
    const shouldSkip = !hasNonGotoCall && info.userCalls.length > 0;
    if (shouldSkip) {
      return;
    }
    const normalized = info.text.replace(/\s+/g, ' ').trim().replace(/;$/, '');
    instructions.push({
      line: info.startLine,
      source: info.text,
      summary: normalized,
    });
  });
  return instructions;
}

function buildPromptMarkdown(output: ClassificationOutput): string {
  const lines: string[] = [
    '# NovaAct beforeEach prompts',
    '',
    `Gerado automaticamente em ${output.generatedAt}.`,
    '',
    'Use os passos abaixo para preparar o ambiente do agente NovaAct antes de cada teste. `page.goto` foi omitido porque já é tratado pelo executor.',
    '',
  ];

  const entriesWithPrompts = output.entries.filter(entry => entry.userInstructions.length > 0);
  if (!entriesWithPrompts.length) {
    lines.push('_Nenhum beforeEach com ações de usuário foi encontrado._');
    return lines.join('\n');
  }

  for (const entry of entriesWithPrompts) {
    lines.push(`## ${entry.file} (linha ${entry.line})`);
    lines.push(`Categoria: ${entry.category}`);
    lines.push('');
    entry.userInstructions.forEach((instruction, index) => {
      lines.push(`${index + 1}. \`${instruction.summary}\``);
    });
    lines.push('');
  }

  return lines.join('\n');
}

function uniqueValues(values: string[]): string[] {
  return Array.from(new Set(values));
}

main();
