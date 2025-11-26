from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from nova_act import ActAgentError, NovaAct, BOOL_SCHEMA
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constantes para detecção de assertions e double-click
# ---------------------------------------------------------------------------
ASSERTION_PREFIX = "verifique"
DOUBLE_CLICK_REGEX = re.compile(
    r"(clique\s+duplo|duplo\s+clique|clique\s+duas\s+vezes|double\s+click)", re.IGNORECASE
)
TEST_ID_REGEX = re.compile(r"test\s*-?\s*id\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Modelo Pydantic para resultado de assertions
# ---------------------------------------------------------------------------
class AssertResult(BaseModel):
    """Modelo para validar respostas de assertions do NovaAct."""

    assert_name: str = Field(..., description="The name of the assertion")
    result: bool = Field(..., description="Whether the assertion passed or not")


try:  # Permite execução direta via `python execute_nova_act.py`
    from .config_loader import TestCaseDefinition
except ImportError:  # pragma: no cover
    from config_loader import TestCaseDefinition

LOGGER = logging.getLogger(__name__)


@dataclass
class NovaActResult:
    name: str
    actual: bool  # Resultado do teste (True/False) - faz par com expected
    duration_seconds: float
    expected: Any
    response: Any  # Resposta bruta do NovaAct
    error: Optional[str] = None
    artifacts_dir: Optional[Path] = None
    assertion_results: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "name": self.name,
            "expected": self.expected,
            "actual": self.actual,
            "duration_seconds": round(self.duration_seconds, 2),
            "error": self.error,
            "artifacts_dir": str(self.artifacts_dir) if self.artifacts_dir else None,
        }
        # Inclui 'response' apenas quando não há assertions (para evitar redundância)
        if self.assertion_results:
            result["assertion_results"] = self.assertion_results
        else:
            result["response"] = _serialize_response(self.response)
        return result


def _serialize_response(response: Any) -> Any:
    """Converte response do NovaAct para formato serializável em JSON."""
    if response is None:
        return None
    # Se já é um tipo primitivo serializável
    if isinstance(response, (str, int, float, bool, list, dict)):
        return response
    # Se tem parsed_response (ActGetResult), usa ele
    if hasattr(response, "parsed_response"):
        return response.parsed_response
    # Se tem __dict__, converte para dict
    if hasattr(response, "__dict__"):
        return {k: v for k, v in response.__dict__.items() if not k.startswith("_")}
    # Fallback: converte para string
    return str(response)


class NovaActExecutor:
    """Responsável por executar uma lista de prompts usando NovaAct."""

    def __init__(
        self,
        artifacts_root: Path,
        suite_label: str,
        run_id: str,
        headless: bool = True,
        record_video: bool = True,
        ignore_https_errors: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.logger = logger or LOGGER
        self.headless = headless
        self.record_video = record_video
        self.ignore_https_errors = ignore_https_errors
        self.run_root = artifacts_root / suite_label / run_id
        self.run_root.mkdir(parents=True, exist_ok=True)

    def execute(self, test_case: TestCaseDefinition) -> NovaActResult:
        prompts = test_case.config.prompt
        expected = test_case.config.expected

        if not prompts:
            raise ValueError(f"O teste {test_case.name} não possui prompts para execução.")

        test_dir = self.run_root / _slugify(test_case.name)
        test_dir.mkdir(parents=True, exist_ok=True)

        # Separa ações de assertions
        # IMPORTANTE: Cada chamada a nova.act() pode causar refresh da página,
        # então unimos todas as ações em um único prompt grande.
        actions = [p for p in prompts if not _is_assertion(p)]
        assertions = [p for p in prompts if _is_assertion(p)]

        self.logger.info(
            "🚀 Iniciando teste '%s' (%s ações + %s assertions)",
            test_case.name,
            len(actions),
            len(assertions),
        )
        start_time = time.perf_counter()
        response: Any = None
        assertion_results: List[Dict[str, Any]] = []
        assertion_schema = AssertResult.model_json_schema()

        try:
            with NovaAct(
                headless=self.headless,
                starting_page=test_case.config.url,
                logs_directory=str(test_dir),
                record_video=self.record_video,
                ignore_https_errors=self.ignore_https_errors,
            ) as nova:
                # -------------------------------------------------------------
                # FASE 1: Executa ações (com tratamento especial para double-click)
                # -------------------------------------------------------------
                if actions:
                    # Encontra índice do primeiro double-click (se houver)
                    dblclick_idx: Optional[int] = None
                    dblclick_selector: Optional[str] = None

                    for i, action in enumerate(actions):
                        if DOUBLE_CLICK_REGEX.search(action):
                            dblclick_idx = i
                            test_id = _extract_test_id(action)
                            dblclick_selector = (
                                f'[data-testid="{test_id}"]'
                                if test_id
                                else '[data-testid="todo-title"]'
                            )
                            break

                    if dblclick_idx is not None and dblclick_selector:
                        # --- Bloco 1: ações ANTES do double-click ---
                        before_dblclick = actions[:dblclick_idx]
                        if before_dblclick:
                            combined_before = " e ".join(before_dblclick)
                            self.logger.debug(
                                "➡️  Executando ações antes do double-click: %s",
                                combined_before[:150] + "...",
                            )
                            response = nova.act(combined_before, max_steps=15)

                        # --- Double-click manual via Playwright ---
                        self.logger.info(
                            "🖱️  Executando double-click manual no seletor %s", dblclick_selector
                        )
                        try:
                            _attempt_manual_double_click(nova, dblclick_selector)
                            self.logger.info("✅ Double-click manual executado com sucesso")
                        except Exception as dblclick_err:
                            self.logger.warning(
                                "⚠️ Falha no double-click manual (%s). Tentando via agente.",
                                dblclick_err,
                            )
                            # Se falhar, tenta executar a ação do double-click via agente
                            nova.act(actions[dblclick_idx], max_steps=10)

                        # --- Bloco 2: ações APÓS o double-click ---
                        after_dblclick = actions[dblclick_idx + 1 :]
                        if after_dblclick:
                            combined_after = " e ".join(after_dblclick)
                            self.logger.debug(
                                "➡️  Executando ações após o double-click: %s",
                                combined_after[:150] + "...",
                            )
                            response = nova.act(combined_after, max_steps=15)
                    else:
                        # Sem double-click, executa tudo junto
                        combined_actions = " e ".join(actions)
                        self.logger.debug(
                            "➡️  Executando ações combinadas: %s", combined_actions[:200] + "..."
                        )
                        response = nova.act(combined_actions, max_steps=15)

                # -------------------------------------------------------------
                # FASE 2: Executa cada assertion separadamente
                # -------------------------------------------------------------
                for idx, assertion in enumerate(assertions, start=1):
                    self.logger.debug("🔍 Assertion %s/%s: %s", idx, len(assertions), assertion)
                    assert_name = _slugify_assertion(assertion)
                    assertion_prompt = (
                        f"{assertion}. Retorne apenas JSON válido que siga o schema fornecido "
                        f"usando assert_name '{assert_name}' e defina result como true ou false "
                        f"dependendo do estado atual da tela."
                    )
                    act_response = nova.act(
                        assertion_prompt, schema=assertion_schema, max_steps=10
                    )
                    try:
                        parsed = AssertResult.model_validate(act_response.parsed_response)
                        self.logger.info(
                            "✅ Assert '%s' -> %s", parsed.assert_name, parsed.result
                        )
                        assertion_results.append(
                            {"assert_name": parsed.assert_name, "result": parsed.result}
                        )
                        if not parsed.result:
                            self.logger.warning(
                                "⚠️  Assertion '%s' retornou False", parsed.assert_name
                            )
                    except Exception as parse_err:
                        self.logger.warning(
                            "⚠️  Falha ao parsear resultado da assertion: %s", parse_err
                        )
                        assertion_results.append(
                            {"assert_name": assert_name, "result": None, "error": str(parse_err)}
                        )
                    response = act_response

        except ActAgentError as exc:
            duration = time.perf_counter() - start_time
            self.logger.error("❌ Falha ao executar '%s': %s", test_case.name, exc)
            return NovaActResult(
                name=test_case.name,
                actual=False,
                duration_seconds=duration,
                expected=expected,
                response=response,
                error=str(exc),
                artifacts_dir=test_dir,
                assertion_results=assertion_results,
            )

        duration = time.perf_counter() - start_time

        # Valida resultado final baseado nas assertions ou expected
        if assertion_results:
            # Se temos assertions, o sucesso é baseado em todas passarem
            all_passed = all(
                ar.get("result") is True for ar in assertion_results if ar.get("result") is not None
            )
            failed_assertions = [
                ar["assert_name"] for ar in assertion_results if ar.get("result") is False
            ]
            if all_passed:
                success = True
                error_msg = None
            else:
                success = False
                error_msg = f"Assertions falharam: {', '.join(failed_assertions)}"
        else:
            # Caso contrário, usa validação tradicional
            success, error_msg = _validate_expected(expected, response)

        if success:
            self.logger.info("✅ Teste '%s' concluído com êxito em %.2fs", test_case.name, duration)
        else:
            self.logger.warning(
                "⚠️  Teste '%s' falhou na validação final em %.2fs (%s)",
                test_case.name,
                duration,
                error_msg,
            )

        return NovaActResult(
            name=test_case.name,
            actual=success,
            duration_seconds=duration,
            expected=expected,
            response=response,
            error=error_msg,
            artifacts_dir=test_dir,
            assertion_results=assertion_results,
        )


def _validate_expected(expected: Any, response: Any) -> tuple[bool, Optional[str]]:
    """Compara o resultado retornado pelo NovaAct com o esperado declarado."""
    if isinstance(expected, bool):
        matches = bool(response) is expected
        return matches, None if matches else f"Esperado {expected}, recebido {response!r}"

    if isinstance(expected, dict):
        if not isinstance(response, dict):
            return False, "Resposta não é um objeto/dicionário"
        for key, value in expected.items():
            if key not in response:
                return False, f"Chave '{key}' ausente na resposta"
            if response[key] != value:
                return False, f"Valor inesperado para '{key}': {response[key]!r}"
        return True, None

    if isinstance(expected, (int, float, str, list)):
        matches = response == expected
        return matches, None if matches else f"Esperado {expected!r}, recebido {response!r}"

    # fallback: qualquer resposta considerada truthy é sucesso
    if response:
        return True, None
    return False, "Resposta vazia ou falsy"


def _slugify(value: str) -> str:
    """Normaliza nomes para uso em diretórios de artefatos."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return slug or "test"


def _slugify_assertion(text: str) -> str:
    """Converte texto da assertion em identificador limpo."""
    cleaned = re.sub(r"^verifique\s+", "", text.strip(), flags=re.IGNORECASE)
    normalized = unicodedata.normalize("NFKD", cleaned)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return ascii_text or "assertion"


def _extract_test_id(text: str) -> Optional[str]:
    """Extrai test-id do texto do prompt para usar no seletor."""
    match = TEST_ID_REGEX.search(text)
    if match:
        return match.group(1).strip()
    return None


def _attempt_manual_double_click(nova: NovaAct, selector: str) -> None:
    """Executa double-click manualmente via Playwright.

    O NovaAct às vezes falha em double-click, então usamos Playwright diretamente.
    """
    locator = nova.page.locator(selector)
    locator.wait_for(state="visible", timeout=5_000)
    locator.scroll_into_view_if_needed(timeout=2_000)
    box = locator.bounding_box()
    if not box:
        raise RuntimeError(f"Bounding box indisponível para o seletor {selector!r}")
    center_x = box["x"] + box["width"] / 2
    center_y = box["y"] + box["height"] / 2
    nova.page.mouse.dblclick(center_x, center_y)


def _is_assertion(prompt: str) -> bool:
    """Verifica se o prompt é uma assertion (começa com 'verifique')."""
    return prompt.strip().lower().startswith(ASSERTION_PREFIX)
