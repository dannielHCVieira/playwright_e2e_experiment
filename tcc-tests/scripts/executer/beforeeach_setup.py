"""Módulo para aplicar setups de beforeEach antes da execução do NovaAct.

Este módulo lê o arquivo de classificação gerado por `scripts/classifyBeforeEach.ts`
e aplica os scripts necessários (addInitScript, exposeFunction, etc.) na página
antes da navegação.

Uso:
    from beforeeach_setup import apply_beforeeach_setup

    # Dentro do executor, após criar o NovaAct mas antes de navegar:
    apply_beforeeach_setup(nova.page, test_file="show-battery-status.spec.js")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)


def _find_classification_file() -> Path:
    """Localiza o arquivo de classificação no repositório."""
    # Sobe do diretório atual até encontrar examples/beforeeach-classification.json
    current = Path(__file__).resolve().parent
    for _ in range(10):  # Limite para evitar loop infinito
        candidate = current / "examples" / "beforeeach-classification.json"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(
        "Arquivo examples/beforeeach-classification.json não encontrado. "
        "Execute `node scripts/dist/classifyBeforeEach.js` primeiro."
    )


def load_classification() -> dict[str, Any]:
    """Carrega o arquivo de classificação JSON."""
    path = _find_classification_file()
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_script_setup(test_file: str) -> Optional[str]:
    """Retorna o scriptSnippet para um arquivo de teste específico.

    Args:
        test_file: Nome do arquivo ou path parcial (ex: 'show-battery-status.spec.js')

    Returns:
        O scriptSnippet se encontrado, None caso contrário.
    """
    try:
        classification = load_classification()
    except FileNotFoundError as e:
        LOGGER.warning("⚠️ %s", e)
        return None

    for entry in classification.get("entries", []):
        file_path = entry.get("file", "")
        snippet = entry.get("scriptSnippet", "").strip()

        if not snippet:
            continue

        # Match por nome exato ou sufixo
        if file_path == test_file or file_path.endswith(test_file):
            LOGGER.info("📋 Encontrado setup para %s", file_path)
            return snippet

    LOGGER.debug("ℹ️ Nenhum script setup encontrado para %s", test_file)
    return None


def apply_beforeeach_setup(page: Any, test_file: str) -> bool:
    """Aplica o setup de beforeEach na página do Playwright.

    IMPORTANTE: Deve ser chamado ANTES da navegação para a URL do teste,
    pois addInitScript precisa estar registrado antes do carregamento da página.

    Args:
        page: Objeto Page do Playwright (ou nova.page do NovaAct)
        test_file: Nome do arquivo de teste

    Returns:
        True se um setup foi aplicado, False caso contrário.
    """
    snippet = get_script_setup(test_file)
    if not snippet:
        return False

    LOGGER.info("🔧 Aplicando beforeEach setup para %s", test_file)

    # Extrai chamadas addInitScript do snippet
    # O snippet contém código como:
    #   await page.addInitScript(() => { ... });
    #   await page.exposeFunction('logCall', msg => log.push(msg));

    # Para aplicar, precisamos executar via evaluate ou usar addInitScript diretamente
    # Como o snippet já está no formato Playwright, podemos extrair o conteúdo
    # do addInitScript e aplicá-lo

    try:
        # Tenta aplicar usando addInitScript
        _apply_init_scripts(page, snippet)
        LOGGER.info("✅ Setup aplicado com sucesso")
        return True
    except Exception as e:
        LOGGER.error("❌ Falha ao aplicar setup: %s", e)
        return False


def _apply_init_scripts(page: Any, snippet: str) -> None:
    """Aplica os scripts de inicialização extraídos do snippet.

    Esta função processa o snippet e aplica os addInitScript encontrados.
    O snippet vem no formato JavaScript Playwright:
        await page.addInitScript(() => { ...body... });

    Precisamos extrair o body e passá-lo para page.add_init_script() do Python.
    """
    import re

    # Padrão para encontrar addInitScript(() => { ... })
    # Usa um approach mais robusto para capturar blocos com chaves aninhadas
    init_scripts = _extract_init_script_bodies(snippet)

    if not init_scripts:
        LOGGER.warning("⚠️ Nenhum addInitScript encontrado no snippet")
        return

    for idx, script_body in enumerate(init_scripts, 1):
        if script_body.strip():
            LOGGER.debug("📜 Aplicando init script %d: %s...", idx, script_body[:80])
            # Envolve em função auto-executável para manter o escopo
            wrapped_script = f"(() => {{{script_body}}})()"
            page.add_init_script(wrapped_script)


def _extract_init_script_bodies(snippet: str) -> list[str]:
    """Extrai os corpos das funções passadas para addInitScript.

    Lida com chaves aninhadas contando abertura/fechamento.
    """
    results = []
    # Procura por 'addInitScript(() => {' ou 'addInitScript(function() {'
    pattern = r"addInitScript\s*\(\s*(?:\(\)\s*=>|\s*function\s*\(\s*\))\s*\{"

    import re
    for match in re.finditer(pattern, snippet):
        start = match.end() - 1  # Posição do '{'
        body = _extract_balanced_braces(snippet, start)
        if body:
            results.append(body)

    return results


def _extract_balanced_braces(text: str, start: int) -> Optional[str]:
    """Extrai conteúdo entre chaves balanceadas começando em 'start'.

    Args:
        text: Texto completo
        start: Índice do '{' inicial

    Returns:
        Conteúdo entre as chaves (sem as chaves externas), ou None se inválido.
    """
    if start >= len(text) or text[start] != "{":
        return None

    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                # Encontrou o fechamento correspondente
                return text[start + 1 : i]
        i += 1

    return None  # Chaves não balanceadas


def get_user_instructions(test_file: str) -> list[str]:
    """Retorna as instruções de usuário (não-script) para um arquivo de teste.

    Útil para adicionar ao prompt do NovaAct como passos preliminares.

    Args:
        test_file: Nome do arquivo de teste

    Returns:
        Lista de instruções textuais (ex: ["await page.locator('...').click()"])
    """
    try:
        classification = load_classification()
    except FileNotFoundError:
        return []

    for entry in classification.get("entries", []):
        file_path = entry.get("file", "")

        if file_path == test_file or file_path.endswith(test_file):
            instructions = entry.get("userInstructions", [])
            return [inst.get("summary", "") for inst in instructions if inst.get("summary")]

    return []
