## Executor NovaAct

O diretório `scripts/executer` contém um runner que lê os prompts em JSON/YAML (por exemplo, aqueles em `tests/example/**/config`) e executa os testes usando o NovaAct. Ele foi pensado para uso local e também para rodar em paralelo ao pipeline padrão no GitHub Actions.

### Dependências

O `uv sync` padrão instala somente o que o NovaAct precisa (sem LangChain).

- Para executar os runners NovaAct: `uv sync`
- Para usar o conversor code2NL localmente: `uv sync --extra converter`
- Para rodar o executor MCP localmente: `uv sync --extra mcp`

### Como executar

```
cd tcc-tests
uv run python scripts/executer/execute_nova_act.py --suite @example/todomvc
```

Principais opções:

- `--suite @example/<nome>`: aponta automaticamente para `tests/example/<nome>/config/prompt.(json|yml)`.
- `--config <caminho>`: usa um arquivo JSON/YAML qualquer (ou diretório contendo `prompt.*`).
- `--test-name SUBSTRING`: filtra testes pelo nome (pode repetir).
- `--dry-run`: apenas lista os testes encontrados.
- `--logs-dir`: muda o diretório raiz de artefatos (default `tcc-tests/artifacts`).
- `--output-json`: exporta um relatório consolidado em JSON.
- `--ignore-https-errors`: ignora validações de certificado HTTPS (útil para servidores locais em `http://`). Também pode ser habilitado com `NOVA_ACT_IGNORE_HTTPS_ERRORS=1`.

Toda execução cria a estrutura `tcc-tests/artifacts/<suite>/<YYYYmmdd-HHMMSS>/<nome-do-teste>/`, contendo os arquivos que o NovaAct gerar (vídeos, logs etc.).

### Integração com GitHub Actions

Criar um job específico facilita rodar os testes NovaAct em paralelo ao job tradicional. Exemplo:

```yaml
jobs:
  nova-act-tests:
    runs-on: ubuntu-latest
    needs: playwright-tests
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - working-directory: tcc-tests
        run: uv sync
      - name: Execute NovaAct suite
        working-directory: tcc-tests
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          uv run python scripts/executer/execute_nova_act.py \
            --suite @example/todomvc \
            --logs-dir artifacts \
            --output-json artifacts/nova-act-report.json
      - uses: actions/upload-artifact@v4
        with:
          name: nova-act-artifacts
          path: tcc-tests/artifacts
```

Adapte o nome da suíte, secrets e caminhos conforme necessário. O job falha caso algum teste retorne status diferente de sucesso, mantendo o PR transparente quanto ao resultado do NovaAct.
