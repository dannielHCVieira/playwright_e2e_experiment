"""
Executor de testes usando Playwright MCP + LLM via mcp-use.

Este executor é uma alternativa ao NovaActExecutor, usando o protocolo MCP
para conectar um LLM (OpenAI/Anthropic) ao Playwright.

Uso:
    python -m scripts.executer.mcp_executor --suite @example/todomvc --test-name "edit-completed"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# mcp-use: biblioteca que conecta LLMs a servidores MCP
# pip install mcp-use langchain-openai langchain-anthropic
try:
    from mcp_use import MCPAgent, MCPClient
except ImportError:
    raise ImportError(
        "Instale as dependências: uv add mcp-use langchain-openai langchain-anthropic"
    )

try:
    from .config_loader import TestCaseDefinition, load_test_cases
except ImportError:
    from config_loader import TestCaseDefinition, load_test_cases

try:
    from .beforeeach_setup import get_init_script_bodies, infer_beforeeach_file
except ImportError:
    from beforeeach_setup import get_init_script_bodies, infer_beforeeach_file

from dotenv import load_dotenv
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

load_dotenv()

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuração do MCP Server
# ---------------------------------------------------------------------------
def get_mcp_config(
    screenshots_dir: Path,
    headless: bool = True,
    init_scripts: Optional[Sequence[Path]] = None,
) -> dict:
    """Retorna configuração do MCP com diretório de screenshots e init scripts opcionais."""
    args = [
        "@playwright/mcp@latest",
        "--isolated",  # Cada sessão é independente (sem persistir cookies/localStorage)
        "--output-dir",
        str(screenshots_dir.absolute()),
    ]
    if headless:
        args.insert(1, "--headless")  # Insere após o pacote npm

    if init_scripts:
        for script_path in init_scripts:
            resolved = Path(script_path).resolve()
            args.append(f"--init-script={resolved}")

    return {
        "mcpServers": {
            "playwright": {
                "command": "npx",
                "args": args,
            }
        }
    }


# ---------------------------------------------------------------------------
# Preços por 1M tokens (em USD) - Atualizado Nov 2024
# ---------------------------------------------------------------------------
TOKEN_PRICES = {
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    # Anthropic
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
    "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
    "claude-3-sonnet-20240229": {"input": 3.00, "output": 15.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calcula o custo em USD baseado no modelo e tokens usados."""
    # Tenta encontrar o modelo exato ou uma correspondência parcial
    prices = TOKEN_PRICES.get(model)
    if not prices:
        # Busca por correspondência parcial
        for model_name, model_prices in TOKEN_PRICES.items():
            if model_name in model or model in model_name:
                prices = model_prices
                break

    if not prices:
        # Fallback para preços médios se modelo desconhecido
        prices = {"input": 5.00, "output": 15.00}

    input_cost = (input_tokens / 1_000_000) * prices["input"]
    output_cost = (output_tokens / 1_000_000) * prices["output"]
    return round(input_cost + output_cost, 6)


# ---------------------------------------------------------------------------
# Modelos de Resultado
# ---------------------------------------------------------------------------
@dataclass
class TokenUsage:
    """Uso de tokens em uma execução."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


@dataclass
class MCPTestResult:
    """Resultado de um teste executado via MCP."""

    name: str
    url: str
    expected: Any
    actual: bool
    duration_seconds: float
    steps_executed: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    screenshots: List[str] = field(default_factory=list)
    screenshots_dir: Optional[str] = None
    token_usage: Optional[TokenUsage] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.expected == self.actual,
            "duration_seconds": round(self.duration_seconds, 2),
            "steps_executed": self.steps_executed,
            "error": self.error,
            "screenshots": self.screenshots,
            "screenshots_dir": self.screenshots_dir,
            "token_usage": self.token_usage.to_dict() if self.token_usage else None,
        }


# ---------------------------------------------------------------------------
# Executor MCP
# ---------------------------------------------------------------------------
class PlaywrightMCPExecutor:
    """Executa testes usando Playwright MCP + LLM."""

    def __init__(
        self,
        llm_provider: str = "openai",
        llm_model: str = "gpt-5-mini",
        artifacts_dir: Path = Path("artifacts/mcp-tests"),
        max_steps: int = 30,
        headless: bool = True,
    ):
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.artifacts_dir = artifacts_dir
        self.max_steps = max_steps
        self.headless = headless
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _is_rate_limit_exception(exc: Exception) -> bool:
        """Detecta erros relacionados a rate limit."""
        message = str(exc).lower()
        if "rate limit" in message or "rate_limit" in message:
            return True

        status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
        if status == 429:
            return True

        response = getattr(exc, "response", None)
        response_status = getattr(response, "status_code", None)
        if response_status == 429:
            return True

        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error") or {}
            if error.get("code") == "rate_limit_exceeded":
                return True

        return False

    async def _run_agent_with_retry(self, agent: MCPAgent, prompt: str):
        """Executa o agente com retry exponencial em caso de rate limit."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(self._is_rate_limit_exception),
            wait=wait_random_exponential(multiplier=2, max=90),
            stop=stop_after_attempt(6),
            before_sleep=before_sleep_log(LOGGER, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                LOGGER.debug(
                    "Executando MCP agent (tentativa %d)",
                    attempt.retry_state.attempt_number,
                )
                return await agent.run(prompt)

    def _create_llm(self):
        """Cria a instância do LLM baseado no provider configurado."""
        if self.llm_provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=self.llm_model)
        elif self.llm_provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(model=self.llm_model)
        else:
            raise ValueError(f"LLM provider não suportado: {self.llm_provider}")

    def _prepare_beforeeach_init_scripts(
        self, test_case: TestCaseDefinition, test_slug: str, base_dir: Path
    ) -> List[Path]:
        """Gera arquivos temporários com init scripts de beforeEach para o MCP."""
        spec_file = infer_beforeeach_file(test_case.name)
        if not spec_file:
            return []

        init_bodies = get_init_script_bodies(spec_file)
        if not init_bodies:
            return []

        scripts_dir = base_dir / "beforeeach"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        script_paths: List[Path] = []
        for idx, body in enumerate(init_bodies, start=1):
            script_path = scripts_dir / f"{test_slug}_init_{idx}.js"
            wrapped = f"(() => {{\n{body}\n}})();\n"
            script_path.write_text(wrapped, encoding="utf-8")
            script_paths.append(script_path.resolve())

        LOGGER.info(
            "🔧 beforeEach setup detectado para %s (%d init script%s)",
            spec_file,
            len(script_paths),
            "s" if len(script_paths) > 1 else "",
        )
        return script_paths

    async def execute_test(self, test_case: TestCaseDefinition) -> MCPTestResult:
        """Executa um único teste usando MCP."""
        LOGGER.info("🚀 Iniciando teste: %s", test_case.name)
        start_time = time.perf_counter()
        steps_executed = []
        error = None
        screenshots = []
        token_usage = None

        # Cria diretório específico para screenshots deste teste
        test_slug = self._slugify(test_case.name)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        screenshots_dir = self.artifacts_dir / f"{test_slug}_{timestamp}"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        init_script_paths = self._prepare_beforeeach_init_scripts(
            test_case, test_slug, screenshots_dir
        )

        client = None
        try:
            # Cria cliente MCP com diretório de screenshots e init scripts opcionais
            mcp_config = get_mcp_config(
                screenshots_dir,
                headless=self.headless,
                init_scripts=init_script_paths,
            )
            client = MCPClient.from_dict(mcp_config)

            # Cria LLM
            llm = self._create_llm()

            # Cria agente MCP
            agent = MCPAgent(llm=llm, client=client, max_steps=self.max_steps)

            # Monta o prompt completo com todas as instruções
            full_prompt = self._build_prompt(test_case, test_slug)
            LOGGER.debug("📝 Prompt: %s", full_prompt[:500])

            # Executa o agente com tracking de tokens
            if self.llm_provider == "openai":
                from langchain_community.callbacks import get_openai_callback

                with get_openai_callback() as cb:
                    result = await self._run_agent_with_retry(agent, full_prompt)
                    token_usage = TokenUsage(
                        input_tokens=cb.prompt_tokens,
                        output_tokens=cb.completion_tokens,
                        total_tokens=cb.total_tokens,
                        cost_usd=cb.total_cost,
                    )
            else:
                # Para Anthropic, usamos estimativa baseada no resultado
                result = await self._run_agent_with_retry(agent, full_prompt)
                # Estima tokens (aproximação: 4 caracteres = 1 token)
                input_chars = len(full_prompt)
                output_chars = len(str(result))
                input_tokens = input_chars // 4
                output_tokens = output_chars // 4
                token_usage = TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    cost_usd=calculate_cost(self.llm_model, input_tokens, output_tokens),
                )

            LOGGER.info("✅ Teste executado: %s", test_case.name)
            if token_usage:
                LOGGER.info(
                    "💰 Tokens: %d (in: %d, out: %d) | Custo: $%.6f",
                    token_usage.total_tokens,
                    token_usage.input_tokens,
                    token_usage.output_tokens,
                    token_usage.cost_usd,
                )

            # Analisa resultado
            actual = self._parse_result(result, test_case.config.expected)
            steps_executed.append({"prompt": full_prompt, "result": str(result)})

            # Coleta screenshots gerados
            screenshots = self._collect_screenshots(screenshots_dir)
            if screenshots:
                LOGGER.info("📸 Screenshots salvos: %d arquivos", len(screenshots))

        except Exception as exc:
            LOGGER.error("❌ Erro no teste %s: %s", test_case.name, exc)
            error = str(exc)
            actual = False

        finally:
            # Fecha o cliente MCP para liberar recursos e garantir isolamento
            if client:
                try:
                    await client.close()
                    LOGGER.debug("🔒 Cliente MCP fechado")
                except Exception:
                    pass  # Ignora erros ao fechar

        duration = time.perf_counter() - start_time

        return MCPTestResult(
            name=test_case.name,
            url=test_case.config.url,
            expected=test_case.config.expected,
            actual=actual,
            duration_seconds=duration,
            steps_executed=steps_executed,
            error=error,
            token_usage=token_usage,
            screenshots=screenshots,
            screenshots_dir=str(screenshots_dir) if screenshots else None,
        )

    def _slugify(self, text: str) -> str:
        """Converte texto em slug para nome de diretório."""
        import re
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower()
        return slug[:60] if slug else "test"

    def _collect_screenshots(self, directory: Path) -> List[str]:
        """Coleta todos os screenshots PNG/JPEG do diretório."""
        screenshots = []
        for ext in ["*.png", "*.jpeg", "*.jpg"]:
            screenshots.extend(str(p) for p in directory.glob(ext))
        return sorted(screenshots)

    def _build_prompt(self, test_case: TestCaseDefinition, test_slug: str) -> str:
        """Constrói o prompt completo para o agente MCP."""
        steps = "\n".join(f"- {step}" for step in test_case.config.prompt)

        return f"""
Você é um testador de software. Execute os seguintes passos no navegador e verifique o resultado.

URL: {test_case.config.url}

Passos a executar:
{steps}

IMPORTANTE:
1. Primeiro, navegue para a URL usando browser_navigate
2. Execute cada passo na ordem indicada
3. Para verificações (passos que começam com "Verifique"), use browser_snapshot para verificar o estado
4. SCREENSHOTS OBRIGATÓRIOS:
   - Tire um screenshot ANTES de começar as ações, com filename "{test_slug}_01_inicial.png"
   - Tire um screenshot DEPOIS de cada ação importante, incrementando o número (ex: "{test_slug}_02_acao.png", "{test_slug}_03_acao.png")
   - Tire um screenshot FINAL ao terminar, com filename "{test_slug}_final.png"
5. Ao final, responda com "PASSED" se todas as verificações passaram, ou "FAILED" seguido do motivo se alguma falhou.
"""

    def _parse_result(self, result: Any, expected: Any) -> bool:
        """Analisa o resultado do agente para determinar sucesso/falha."""
        result_str = str(result).upper()

        if "PASSED" in result_str:
            return True
        if "FAILED" in result_str:
            return False

        # Se expected é bool, tenta inferir do resultado
        if isinstance(expected, bool):
            # Se não houve erro explícito, considera sucesso
            return "ERROR" not in result_str and "FALHOU" not in result_str

        return bool(result)

    async def execute_suite(
        self, test_cases: List[TestCaseDefinition]
    ) -> List[MCPTestResult]:
        """Executa uma suíte de testes."""
        results = []
        for idx, test_case in enumerate(test_cases, 1):
            LOGGER.info(
                "📋 Executando teste %d/%d: %s", idx, len(test_cases), test_case.name
            )
            result = await self.execute_test(test_case)
            results.append(result)
        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Executa testes usando Playwright MCP + LLM"
    )
    parser.add_argument(
        "--suite",
        required=True,
        help="Alias da suíte (ex: @example/todomvc)",
    )
    parser.add_argument(
        "--test-name",
        action="append",
        default=[],
        help="Filtra testes por nome (pode repetir)",
    )
    parser.add_argument(
        "--llm-provider",
        default="openai",
        choices=["openai", "anthropic"],
        help="Provider do LLM",
    )
    parser.add_argument(
        "--llm-model",
        default="gpt-5-mini",
        help="Modelo do LLM (ex: gpt-5-mini, claude-4-5-haiku-20251001)",
    )
    parser.add_argument(
        "--output-json",
        help="Diretório para salvar relatório JSON",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista testes sem executar",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Roda o navegador em modo visível (não-headless)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    # Carrega testes
    test_cases = load_test_cases(args.suite)

    # Filtra por nome
    if args.test_name:
        lowered = [n.lower() for n in args.test_name]
        test_cases = [
            tc
            for tc in test_cases
            if any(f in tc.name.lower() for f in lowered)
        ]

    if not test_cases:
        LOGGER.error("Nenhum teste encontrado")
        return 1

    if args.dry_run:
        print("=== DRY-RUN (MCP Executor) ===")
        for tc in test_cases:
            print(f"- {tc.name} ({len(tc.config.prompt)} passos)")
        return 0

    # Executa
    executor = PlaywrightMCPExecutor(
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        headless=not args.headed,
    )
    results = await executor.execute_suite(test_cases)

    # Sumário
    passed = sum(1 for r in results if r.actual == r.expected)
    failed = len(results) - passed
    total_time = sum(r.duration_seconds for r in results)

    # Totais de tokens e custo
    total_input_tokens = sum(r.token_usage.input_tokens for r in results if r.token_usage)
    total_output_tokens = sum(r.token_usage.output_tokens for r in results if r.token_usage)
    total_tokens = sum(r.token_usage.total_tokens for r in results if r.token_usage)
    total_cost = sum(r.token_usage.cost_usd for r in results if r.token_usage)

    print(f"\n{'='*60}")
    print(f"📊 RESULTADOS (MCP Executor)")
    print(f"{'='*60}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"⏱️  Tempo total: {total_time:.2f}s")
    print(f"{'='*60}")
    print(f"💰 CUSTO DE TOKENS")
    print(f"{'='*60}")
    print(f"   Input tokens:  {total_input_tokens:,}")
    print(f"   Output tokens: {total_output_tokens:,}")
    print(f"   Total tokens:  {total_tokens:,}")
    print(f"   Custo total:   ${total_cost:.6f} USD")
    print(f"{'='*60}")

    # Salva relatório
    if args.output_json:
        output_dir = Path(args.output_json)
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        report_file = output_dir / f"mcp-report_{timestamp}.json"

        report = {
            "executor": "PlaywrightMCPExecutor",
            "llm_provider": args.llm_provider,
            "llm_model": args.llm_model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(results),
                "passed": passed,
                "failed": failed,
                "duration_seconds": round(total_time, 2),
            },
            "token_usage_total": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": round(total_cost, 6),
            },
            "results": [r.to_dict() for r in results],
        }

        with open(report_file, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        LOGGER.info("📄 Relatório salvo: %s", report_file)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
