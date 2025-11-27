# Automação de `test.beforeEach` nos exemplos

Este diretório agora conta com uma rotina para identificar e classificar automaticamente os blocos `test.beforeEach` dos projetos em `examples/**/tests/**`. O objetivo é separar responsabilidades entre:

- **Scripts de mock/DOM** – precisam ser executados antes do `execute_nova_act.py`.
- **Passos de usuário** – viram instruções textuais que o agente NovaAct deve seguir (sem repetir `page.goto`).
- **Blocos mistos** – têm ambas as partes, que são divididas automaticamente.

## Como gerar os artefatos

1. Compile os utilitários TypeScript:
   ```bash
   npx tsc -p scripts/tsconfig.json
   ```
2. Execute o classificador:
   ```bash
   node scripts/dist/classifyBeforeEach.js
   ```

Esses comandos produzem dois arquivos versionados:

- `examples/beforeeach-classification.json`: dados estruturados com categoria, trechos de script (`scriptSnippet`) e instruções de usuário.
- `examples/beforeeach-prompts.md`: lista em Markdown dos passos que devem ser copiados para o prompt do NovaAct.

## Preparando o ambiente antes do NovaAct

O módulo `scripts/pre_nova_setup.ts` (e o compilado em `scripts/dist/pre_nova_setup.js`) expõe helpers para executar automaticamente os trechos marcados como script:

```ts
import { runScriptSetups } from '../scripts/pre_nova_setup';

// Para um arquivo específico (recomendado):
await runScriptSetups({
  page,
  exactFile: 'show-battery-status.spec.js',
  logger: console.log,
});

// Ou para filtrar por substring (pode dar conflito se múltiplos arquivos tiverem setups incompatíveis):
await runScriptSetups({
  page,
  fileIncludes: 'examples/mock-battery/tests',
  logger: console.log,
});
```

### Opções disponíveis

- `exactFile` – filtra por caminho exato do arquivo (tem prioridade). Aceita nome do arquivo ou path completo.
- `fileIncludes` – aceita uma string ou lista de substrings para filtrar quais arquivos terão o setup executado.
- `runScriptSetups` usa o `scriptSnippet` diretamente, então rode o classificador sempre que os testes mudarem.

### Script de validação

Para testar o setup de um arquivo específico via linha de comando:

```bash
# Roda setup de um arquivo específico
node scripts/run_mock_battery_setup.mjs show-battery-status.spec.js

# Sem argumento, roda todos (pode dar conflito de funções já registradas)
node scripts/run_mock_battery_setup.mjs
```

## Integração com NovaAct

O executor `tcc-tests/scripts/executer/execute_nova_act.py` agora suporta aplicar os scripts de beforeEach automaticamente.

### Detecção automática

O executor infere automaticamente o arquivo de beforeEach a partir do nome do teste. Se o teste segue o padrão `{spec-file}__{num}-{test-name}`, ele detecta o arquivo `.spec.js` ou `.spec.ts` correspondente.

Exemplo: `show-battery-status__01-show-battery-status` → `show-battery-status.spec.js`

### Uso via linha de comando

```bash
# Detecção automática (recomendado)
python -m scripts.executer.execute_nova_act \
  --suite @example/mock-battery

# Ou especificando manualmente o arquivo de beforeEach
python -m scripts.executer.execute_nova_act \
  --config path/to/config.yml \
  --beforeeach-test-file show-battery-status.spec.js
```

### Uso programático

```python
from tcc_tests.scripts.executer.beforeeach_setup import apply_beforeeach_setup, get_script_setup

# Verificar se existe setup para um arquivo
snippet = get_script_setup("show-battery-status.spec.js")
if snippet:
    print("Setup encontrado!")

# Aplicar setup em uma página Playwright
apply_beforeeach_setup(page, "show-battery-status.spec.js")
```

## Validação e manutenção

- Sempre que novos testes forem adicionados em `examples/**/tests`, reexecute o classificador para manter os arquivos derivados atualizados.
- Caso surjam APIs novas em `beforeEach`, atualize as listas `SCRIPT_APIS` ou `USER_APIS` em `scripts/classifyBeforeEach.ts`.
- Se o `execute_nova_act.py` ou outro consumidor precisar de dados adicionais, eles podem ser obtidos diretamente de `beforeeach-classification.json`.
