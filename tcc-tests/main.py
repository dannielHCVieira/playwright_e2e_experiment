from __future__ import annotations
import os

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import patch_requests
from nova_act import ActAgentError, NovaAct

try:  # Permite execução direta via `python execute_nova_act.py`
    from .config_loader import TestCaseDefinition
except ImportError:  # pragma: no cover
    from scripts.executer.config_loader import TestCaseDefinition

from pydantic import BaseModel, Field
from dotenv import load_dotenv

LOGGER = logging.getLogger(__name__)
ASSERTION_PREFIX = "verifique"
ASSERTION_SPLIT_REGEX = re.compile(r"(?=verifique)", re.IGNORECASE)
SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+")
DOUBLE_CLICK_REGEX = re.compile(r"(clique\s+duplo|double\s+click)", re.IGNORECASE)
TEST_ID_REGEX = re.compile(r"test\s*-?\s*id\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)


class AssertResult(BaseModel):
    assert_name: str = Field(..., description="The name of the assertion")
    result: bool = Field(..., description="Whether the test was successful or not")


@dataclass
class ActionStep:
    text: str
    is_assertion: bool = False


def slugify_assertion(text: str) -> str:
    cleaned = re.sub(r"^verifique\s+", "", text.strip(), flags=re.IGNORECASE)
    normalized = unicodedata.normalize("NFKD", cleaned)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return ascii_text or "assertion"


def _cleanup_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" ,.")


def _cleanup_assertion(text: str) -> str:
    cleaned = _cleanup_text(text)
    cleaned = re.sub(r"(?:e\s+ent[aã]o)$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def split_prompt_into_steps(prompt: str) -> list[ActionStep]:
    compact_prompt = re.sub(r"\s+", " ", prompt).strip()
    if not compact_prompt:
        return []

    steps: list[ActionStep] = []
    for segment in ASSERTION_SPLIT_REGEX.split(compact_prompt):
        segment = segment.strip()
        if not segment:
            continue
        if segment.lower().startswith(ASSERTION_PREFIX):
            steps.append(ActionStep(_cleanup_assertion(segment), True))
        else:
            for sentence in SENTENCE_SPLIT_REGEX.split(segment):
                sentence = sentence.strip(" ,.")
                if sentence:
                    steps.append(ActionStep(sentence, False))
    return steps


def build_steps(prompts: list[str]) -> list[ActionStep]:
    steps: list[ActionStep] = []
    for prompt in prompts:
        steps.extend(split_prompt_into_steps(prompt))
    return steps


def _extract_test_id(text: str) -> Optional[str]:
    match = TEST_ID_REGEX.search(text)
    if match:
        return match.group(1).strip()
    return None


def _attempt_manual_double_click(nova: NovaAct, selector: str) -> None:
    locator = nova.page.locator(selector)
    locator.wait_for(state="visible", timeout=5_000)
    locator.scroll_into_view_if_needed(timeout=2_000)
    box = locator.bounding_box()
    if not box:
        raise RuntimeError(f"Bounding box indisponível para o seletor {selector!r}")
    center_x = box["x"] + box["width"] / 2
    center_y = box["y"] + box["height"] / 2
    nova.page.mouse.dblclick(center_x, center_y)


def test():
    headless = False
    url = "https://demo.playwright.dev/todomvc/"

    prompts = [
        """Digite 'Buy groceries' no campo de texto 'What needs to be done?' e pressione 'Enter' para confirmar,
        clique na caixa de seleção 'Toggle Todo' para marcar o todo como concluído.""","""
        Dê um clique duplo no texto do todo com Test ID 'todo-title' para ativar a edição e então, 
        digite 'Buy groceries and milk' no campo de texto 'Edit' e finalmente, 
        pressione 'Enter' no campo de texto 'Edit'.
        Verifique se o texto 'Buy groceries and milk' está visível e então, 
        verifique se a caixa de seleção 'Toggle Todo' está marcada (o todo permanece como concluído)."""
    ]

    steps = build_steps(prompts)
    assertion_schema = AssertResult.model_json_schema()
    assertion_results: list[AssertResult] = []

    with NovaAct(
        headless=headless,
        starting_page=url,
        logs_directory="./logs/",
        record_video=True
    ) as nova:
        for idx, step in enumerate(steps, start=1):
            LOGGER.debug("➡️  Passo %s/%s: %s", idx, len(steps), step.text)
            if step.is_assertion:
                assert_name = slugify_assertion(step.text)
                assertion_prompt = (
                    f"{step.text}. Retorne apenas JSON válido que siga o schema fornecido "
                    f"usando assert_name '{assert_name}' e defina result como true ou false "
                    f"dependendo do estado atual da tela."
                )
                response = nova.act_get(assertion_prompt, schema=assertion_schema, max_steps=10)
                parsed = AssertResult.model_validate(response.parsed_response)
                LOGGER.info("✅ Assert %s -> %s", parsed.assert_name, parsed.result)
                assertion_results.append(parsed)
            else:
                if DOUBLE_CLICK_REGEX.search(step.text):
                    selector = '[data-testid="todo-title"]'
                    test_id = _extract_test_id(step.text)
                    if test_id:
                        selector = f'[data-testid="{test_id}"]'
                    try:
                        _attempt_manual_double_click(nova, selector)
                        LOGGER.info("✅ Duplo clique manual aplicado no seletor %s (agente continuará a etapa).", selector)
                    except Exception as err:  # pragma: no cover - caminho defensivo
                        LOGGER.warning("⚠️ Falha no duplo clique manual (%s). Delegando 100%% ao agente.", err)
                nova.act(step.text, max_steps=10)

    return assertion_results


if __name__ == "__main__":
    load_dotenv()
    print(test())
