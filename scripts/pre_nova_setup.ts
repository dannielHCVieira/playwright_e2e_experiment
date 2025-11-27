import fs from 'fs';
import path from 'path';

type Category = 'script' | 'user' | 'mixed' | 'neutral';

interface ClassificationEntry {
  file: string;
  line: number;
  category: Category;
  scriptSnippet: string;
}

interface ClassificationFile {
  generatedAt: string;
  entries: ClassificationEntry[];
}

export interface ScriptSetupEntry {
  file: string;
  line: number;
  category: Category;
  snippet: string;
}

export interface LoadOptions {
  /** Filtra arquivos que contenham qualquer das substrings */
  fileIncludes?: string | string[];
  /** Filtra por caminho exato do arquivo (tem prioridade sobre fileIncludes) */
  exactFile?: string;
}

export interface RunScriptSetupOptions extends LoadOptions {
  page: unknown;
  logger?: (message: string) => void;
}

const CLASSIFICATION_PATH = path.join('examples', 'beforeeach-classification.json');

export function listScriptSetups(options?: LoadOptions): ScriptSetupEntry[] {
  const classification = readClassificationFile();
  const exactFile = options?.exactFile;
  const filters = normalizeFilters(options?.fileIncludes);

  return classification.entries
    .filter(entry => entry.scriptSnippet && entry.scriptSnippet.trim())
    .filter(entry => {
      // Se exactFile foi fornecido, usa match exato (prioridade)
      if (exactFile) {
        return entry.file === exactFile || entry.file.endsWith(exactFile);
      }
      // Caso contrário, usa substring matching
      return !filters.length || filters.some(filter => entry.file.includes(filter));
    })
    .map(entry => ({
      file: entry.file,
      line: entry.line,
      category: entry.category,
      snippet: entry.scriptSnippet.trim(),
    }));
}

export async function runScriptSetups(options: RunScriptSetupOptions): Promise<void> {
  const setups = listScriptSetups(options);
  if (!setups.length) {
    options.logger?.('Nenhum setup de script foi encontrado para os filtros fornecidos.');
    return;
  }
  for (const setup of setups) {
    options.logger?.(`Executando setup de ${setup.file}:${setup.line}`);
    await executeSnippet(setup.snippet, options.page);
  }
}

function executeSnippet(snippet: string, page: unknown): Promise<unknown> {
  const runner = new Function(
    'page',
    [
      'return (async () => {',
      snippet,
      '})();',
    ].join('\n'),
  );
  return Promise.resolve(runner(page));
}

function readClassificationFile(): ClassificationFile {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const absolutePath = path.join(repoRoot, CLASSIFICATION_PATH);
  if (!fs.existsSync(absolutePath)) {
    throw new Error(`Arquivo de classificação não encontrado em ${absolutePath}. Rode scripts/classifyBeforeEach.ts primeiro.`);
  }
  const data = fs.readFileSync(absolutePath, 'utf-8');
  return JSON.parse(data) as ClassificationFile;
}

function normalizeFilters(value?: string | string[]): string[] {
  if (!value) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
}
