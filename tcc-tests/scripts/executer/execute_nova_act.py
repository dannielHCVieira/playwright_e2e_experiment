from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Sequence

try:  # Permite executar como script direto
    from .config_loader import ConfigLoaderError, TestCaseDefinition, load_test_cases
    from .nova_executor import NovaActExecutor, NovaActResult
except ImportError:  # pragma: no cover
    current_dir = Path(__file__).resolve().parent
    if str(current_dir) not in sys.path:
        sys.path.append(str(current_dir))
    from config_loader import ConfigLoaderError, TestCaseDefinition, load_test_cases
    from nova_executor import NovaActExecutor, NovaActResult


def _env_flag(env_var: str, default: bool = False) -> bool:
    """Retorna True quando env estiver definido com valor truthy."""
    value = os.getenv(env_var)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa testes NovaAct a partir de arquivos JSON/YAML de configuração.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ref_group = parser.add_mutually_exclusive_group(required=True)
    ref_group.add_argument(
        "--suite",
        help="Alias de suíte no formato @example/<nome> (ex.: @example/todomvc).",
    )
    ref_group.add_argument(
        "--config",
        help="Caminho direto para um arquivo JSON/YAML ou diretório contendo prompt.(json|yml).",
    )
    parser.add_argument(
        "--test-name",
        action="append",
        default=[],
        metavar="SUBSTRING",
        help="Filtra testes cujo nome contenha a substring informada (case-insensitive). Pode ser repetido.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista os testes encontrados sem executar o NovaAct.",
    )
    parser.add_argument(
        "--logs-dir",
        default="tcc-tests/artifacts",
        help="Diretório raiz para armazenar vídeos/logs por execução.",
    )
    parser.add_argument(
        "--output-json",
        help="Se definido, salva o resultado agregado em JSON no caminho informado.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nível de log exibido no console.",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Executa o navegador com interface (modo não headless).",
    )
    parser.add_argument(
        "--rate-limit-batch",
        type=int,
        default=0,
        metavar="N",
        help="Pausa após N testes executados (0 = desativado). Útil para evitar rate limiting da API.",
    )
    parser.add_argument(
        "--rate-limit-sleep",
        type=int,
        default=10,
        metavar="SECONDS",
        help="Segundos de pausa após cada batch (usado com --rate-limit-batch).",
    )
    parser.add_argument(
        "--ignore-https-errors",
        action="store_true",
        default=_env_flag("NOVA_ACT_IGNORE_HTTPS_ERRORS"),
        help=(
            "Ignora validações de certificado HTTPS. Útil para servidores locais em http://."
            " Também pode ser habilitado definindo NOVA_ACT_IGNORE_HTTPS_ERRORS=1."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _configure_logging(args.log_level)

    reference = args.suite or args.config
    try:
        test_cases = load_test_cases(reference)
    except ConfigLoaderError as exc:
        logging.error("Falha ao carregar configurações: %s", exc)
        return 2

    original_total = len(test_cases)
    if original_total == 0:
        logging.error("Nenhum teste encontrado para a referência fornecida.")
        return 2

    filtered_cases = _apply_filters(test_cases, args.test_name)
    skipped_by_filter = original_total - len(filtered_cases)

    if not filtered_cases:
        logging.error("Nenhum teste restante após aplicar os filtros --test-name.")
        return 3

    if args.dry_run:
        _print_dry_run(filtered_cases, skipped_by_filter)
        return 0

    suite_label = _derive_suite_label(reference, filtered_cases[0].source)
    artifacts_root = _resolve_artifacts_root(args.logs_dir)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    executor = NovaActExecutor(
        artifacts_root=artifacts_root,
        suite_label=suite_label,
        run_id=run_id,
        headless=not args.headful,
        ignore_https_errors=args.ignore_https_errors,
        logger=logging.getLogger("nova_act_runner"),
    )

    results: List[NovaActResult] = []
    for idx, case in enumerate(filtered_cases, start=1):
        results.append(executor.execute(case))
        
        # Rate limiting: pausa após cada batch de N testes
        if args.rate_limit_batch > 0 and idx % args.rate_limit_batch == 0:
            remaining = len(filtered_cases) - idx
            if remaining > 0:
                logging.info(
                    "⏸️  Rate limit: pausando %ds após %d testes (%d restantes)...",
                    args.rate_limit_sleep,
                    idx,
                    remaining,
                )
                time.sleep(args.rate_limit_sleep)

    summary = _build_summary(results, original_total, skipped_by_filter)
    _print_summary(summary)

    if args.output_json:
        _write_json_report(args.output_json, summary, results)

    return 0 if summary["failed"] == 0 else 1


def _configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _apply_filters(
    cases: Sequence[TestCaseDefinition], substrings: Sequence[str]
) -> List[TestCaseDefinition]:
    if not substrings:
        return list(cases)

    lowered = [s.lower() for s in substrings]

    def matches(name: str) -> bool:
        candidate = name.lower()
        return any(fragment in candidate for fragment in lowered)

    return [case for case in cases if matches(case.name)]


def _print_dry_run(cases: Sequence[TestCaseDefinition], skipped: int) -> None:
    print("=== DRY-RUN ===")
    for case in cases:
        print(f"- {case.name} ({len(case.config.prompt)} passos) — {case.config.url}")
    print(f"\nTotal listado: {len(cases)} | Ignorados por filtros: {skipped}")


def _resolve_artifacts_root(logs_dir: str) -> Path:
    candidate = Path(logs_dir).expanduser()
    if not candidate.is_absolute():
        tcc_root = Path(__file__).resolve().parents[2]
        candidate = (tcc_root / logs_dir).resolve()
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _derive_suite_label(reference: str, config_path: Path) -> str:
    if reference and reference.startswith("@example/"):
        return reference.split("/", 1)[1]
    if config_path.parent.name == "config" and config_path.parent.parent:
        return config_path.parent.parent.name
    return config_path.stem


def _build_summary(
    results: Sequence[NovaActResult],
    original_total: int,
    skipped_by_filter: int,
) -> dict:
    executed = len(results)
    success = sum(1 for result in results if result.actual)
    failed = executed - success
    duration = sum(result.duration_seconds for result in results)
    return {
        "total_found": original_total,
        "executed": executed,
        "skipped_by_filter": skipped_by_filter,
        "passed": success,
        "failed": failed,
        "duration_seconds": round(duration, 2),
    }


def _print_summary(summary: dict) -> None:
    logging.info(
        "Resumo: %s executados (%s aprovados, %s falhos) | Skipped=%s | Tempo=%.2fs",
        summary["executed"],
        summary["passed"],
        summary["failed"],
        summary["skipped_by_filter"],
        summary["duration_seconds"],
    )


def _write_json_report(output_dir: str, summary: dict, results: Sequence[NovaActResult]) -> None:
    """Salva o relatório JSON no diretório especificado com nome baseado no teste + timestamp."""
    dir_path = Path(output_dir).expanduser()
    if not dir_path.is_absolute():
        dir_path = Path.cwd() / dir_path
    dir_path.mkdir(parents=True, exist_ok=True)

    # Gera nome do arquivo baseado no(s) teste(s) executado(s)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if len(results) == 1:
        test_name = _slugify_filename(results[0].name)
    else:
        # Múltiplos testes: usa o nome do primeiro + quantidade
        test_name = f"{_slugify_filename(results[0].name)}_and_{len(results)-1}_more"

    filename = f"{test_name}_{timestamp}.json"
    file_path = dir_path / filename

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": summary,
        "results": [result.to_dict() for result in results],
    }
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    logging.info("Relatório salvo em %s", file_path)


def _slugify_filename(value: str) -> str:
    """Normaliza nome para uso em nomes de arquivos."""
    import re
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    # Limita o tamanho para evitar nomes muito longos
    return slug[:80] if slug else "test"


if __name__ == "__main__":
    raise SystemExit(main())
