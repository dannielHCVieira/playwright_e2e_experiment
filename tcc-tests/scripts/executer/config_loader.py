from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

LOGGER = logging.getLogger(__name__)


class ConfigLoaderError(RuntimeError):
    """Erro de alto nível para problemas ao carregar configurações."""


class SchemaField(BaseModel):
    """Representa um campo em um schema simples usado pelos prompts."""

    type: str = Field(..., description="Tipo do campo conforme JSON Schema (ex.: string, number)")
    description: str = Field(..., description="Descrição amigável do campo")


class TestConfig(BaseModel):
    """Modelo pydantic para uma configuração de teste individual."""

    name: str = Field(..., description="Nome do teste")
    url: str = Field(..., description="URL inicial usada pelo teste")
    prompt: List[str] = Field(..., description="Sequência ordenada de ações/validações")
    expected: Union[bool, Dict[str, Any], List[Any], str, int, float] = Field(
        ..., description="Resultado esperado para o último passo"
    )
    schema_expected: Optional[Union[str, Dict[str, Union[SchemaField, Any]]]] = Field(
        None,
        description="Schema inline ou caminho para schema externo a ser aplicado no último passo",
    )

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("URL deve começar com http:// ou https://")
        return value

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, prompts: Sequence[str]) -> List[str]:
        if not prompts:
            raise ValueError("Cada teste deve possuir pelo menos um prompt")
        cleaned = [p for p in (item.strip() for item in prompts) if p]
        if not cleaned:
            raise ValueError("Os prompts não podem ser vazios")
        return cleaned


@dataclass(slots=True)
class TestCaseDefinition:
    """Agrupa a configuração validada, schema resolvido e metadados da origem."""

    config: TestConfig
    schema: Optional[Dict[str, Any]]
    source: Path

    @property
    def name(self) -> str:
        return self.config.name


def resolve_config_reference(reference: str) -> Path:
    """Resolve `@example/foo` ou caminhos diretos para um arquivo de configuração."""
    if not reference:
        raise ConfigLoaderError("Nenhuma referência de configuração foi informada.")

    reference = reference.strip()
    tcc_root = Path(__file__).resolve().parents[2]

    if reference.startswith("@example/"):
        suite = reference[len("@example/") :].strip()
        if not suite:
            raise ConfigLoaderError("Referência @example deve seguir o formato @example/<suite>.")
        candidate_dir = tcc_root / "tests" / "example" / suite / "config"
        return _discover_config_file(candidate_dir)

    candidate = Path(reference).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
        if not candidate.exists():
            candidate = (tcc_root / reference).resolve()

    return _discover_config_file(candidate)


def load_test_cases(reference: str) -> List[TestCaseDefinition]:
    """Carrega e valida todos os testes a partir de uma referência."""
    config_path = resolve_config_reference(reference)
    try:
        raw_entries = _read_config_file(config_path)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigLoaderError(f"Falha ao ler {config_path}: {exc}") from exc

    cases: List[TestCaseDefinition] = []
    for index, entry in enumerate(raw_entries, start=1):
        try:
            config = TestConfig(**entry)
        except ValidationError as exc:
            raise ConfigLoaderError(
                f"Erro de validação no item #{index} do arquivo {config_path}: {exc}"
            ) from exc

        schema = _resolve_schema(config.schema_expected, config_path.parent)
        cases.append(TestCaseDefinition(config=config, schema=schema, source=config_path))

    LOGGER.debug("✅ %s testes carregados de %s", len(cases), config_path)
    return cases


def _discover_config_file(path: Path) -> Path:
    """Resolve um diretório ou arquivo em um caminho de configuração suportado."""
    if path.is_file():
        if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            raise ConfigLoaderError(f"Formato não suportado: {path.suffix}")
        return path

    if path.is_dir():
        for candidate_name in ("prompt.json", "prompt.yaml", "prompt.yml"):
            candidate = path / candidate_name
            if candidate.exists():
                return candidate
        raise ConfigLoaderError(
            f"Diretório {path} não contém um arquivo prompt.json/prompt.yaml/prompt.yml."
        )

    raise ConfigLoaderError(f"Caminho de configuração inexistente: {path}")


def _read_config_file(path: Path) -> List[Dict[str, Any]]:
    """Lê um arquivo JSON/YAML e garante que retorne uma lista de objetos."""
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)

    if not isinstance(data, list):
        raise ConfigLoaderError(f"O arquivo {path} deve conter uma lista de testes.")

    return data


def _resolve_schema(
    schema_expected: Optional[Union[str, Dict[str, Union[SchemaField, Any]]]],
    base_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Gera um schema JSON consumível pelo NovaAct."""
    if schema_expected is None:
        return None

    if isinstance(schema_expected, str):
        schema_path = Path(schema_expected).expanduser()
        if not schema_path.is_absolute():
            schema_path = (base_dir / schema_path).resolve()
        if not schema_path.exists():
            raise ConfigLoaderError(f"Schema externo não encontrado: {schema_path}")
        with schema_path.open("r", encoding="utf-8") as handle:
            if schema_path.suffix.lower() == ".json":
                return json.load(handle)
            return yaml.safe_load(handle)

    # Caso já seja um dicionário simples (provavelmente vindo de YAML sem SchemaField)
    if schema_expected and isinstance(next(iter(schema_expected.values()), None), dict):
        first_value = next(iter(schema_expected.values()))
        if not isinstance(first_value, SchemaField):
            return schema_expected  # type: ignore[return-value]

    properties: Dict[str, Dict[str, Any]] = {}
    required_fields: List[str] = []
    for key, value in schema_expected.items():  # type: ignore[union-attr]
        if isinstance(value, SchemaField):
            properties[key] = {"type": value.type, "description": value.description}
        elif isinstance(value, dict):
            properties[key] = value
        else:
            properties[key] = {"description": str(value)}
        required_fields.append(key)

    if not properties:
        return None

    return {"type": "object", "properties": properties, "required": required_fields}
