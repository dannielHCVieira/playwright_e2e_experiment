import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from pydantic import ValidationError

# Importação relativa funciona bem quando executado como módulo (-m)
try:
    from .schemas import ConversionConfig
    from .processor import TestConverter
except ImportError:
    # Fallback para permitir execução direta se o PYTHONPATH estiver configurado
    from scripts.schemas import ConversionConfig
    from scripts.processor import TestConverter

EXTRACTOR_PATH = Path(__file__).resolve().parent / "extract_playwright_tests.js"

load_dotenv()


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "teste"


def extract_tests_from_spec(spec_file: Path) -> List[Dict[str, Any]]:
    if not EXTRACTOR_PATH.exists():
        return []

    result = subprocess.run(
        ["node", str(EXTRACTOR_PATH), str(spec_file)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"⚠️ Falha ao extrair testes de {spec_file}:\n{result.stderr.strip()}")
        return []

    try:
        data = json.loads(result.stdout.strip() or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        print(f"⚠️ Saída inválida do extrator em {spec_file}: {exc}")
        return []


def convert_file(
    spec_file: Path,
    model_name: str,
    output_path: Optional[Path],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    try:
        payload = {
            "input_file": spec_file,
            "output_path": output_path,
            "model_name": model_name,
        }
        if metadata:
            payload.update(metadata)
        config = ConversionConfig(**payload)
        converter = TestConverter(config)
        converter.run()
        return True
    except ValidationError as e:
        print(f"❌ Erro de Validação em {spec_file}:")
        for err in e.errors():
            print(f"- {err['loc'][0]}: {err['msg']}")
        return False
    except Exception as e:
        print(f"❌ Erro ao processar {spec_file}: {e}")
        return False


def process_spec(spec_file: Path, model_name: str) -> bool:
    extracted_tests = extract_tests_from_spec(spec_file)
    if not extracted_tests:
        return convert_file(spec_file, model_name, None)

    all_ok = True
    for test in extracted_tests:
        base_slug = slugify(test.get("fullName") or test.get("name", "teste"))
        index = test.get("index")
        slug = f"{index:02d}-{base_slug}" if isinstance(index, int) else base_slug
        metadata = {
            "test_name": test.get("name"),
            "test_full_name": test.get("fullName"),
            "test_slug": slug,
            "code_snippet": test.get("code"),
        }
        success = convert_file(spec_file, model_name, None, metadata)
        all_ok = all_ok and success
    return all_ok

def main():
    parser = argparse.ArgumentParser(
        description='Converte código Playwright em prompts de linguagem natural.'
    )
    parser.add_argument(
        'target_path',
        type=str,
        help='Caminho do arquivo .spec.ts ou raiz do projeto em examples/<projeto>',
    )
    parser.add_argument(
        '--output',
        type=str,
        help='Caminho de saída opcional (.md). Usado apenas no modo arquivo único.',
        required=False,
    )
    parser.add_argument(
        '--model',
        type=str,
        default='gpt-5-mini',
        help='Modelo OpenAI',
    )

    args = parser.parse_args()
    target_path = Path(args.target_path).resolve()

    if not target_path.exists():
        print(f"❌ Caminho não encontrado: {target_path}")
        sys.exit(1)

    if target_path.is_dir():
        tests_root = target_path / "tests"
        if not tests_root.exists():
            print(f"❌ Diretório de testes não encontrado em: {tests_root}")
            sys.exit(1)

        if args.output:
            print("⚠️ Argumento --output ignorado no modo de diretório (cada arquivo define seu próprio destino).")

        spec_files = sorted(
            list(tests_root.rglob("*.spec.ts")) + list(tests_root.rglob("*.spec.js"))
        )
        if not spec_files:
            print(f"❌ Nenhum arquivo .spec.ts encontrado em {tests_root}")
            sys.exit(1)

        print(f"📁 Processando {len(spec_files)} arquivos de teste em {tests_root}...")
        all_success = True
        for spec_file in spec_files:
            success = process_spec(spec_file, args.model)
            all_success = all_success and success

        if not all_success:
            sys.exit(1)
    else:
        output_path = Path(args.output) if args.output else None
        if target_path.suffix in {".ts", ".js"}:
            if output_path:
                print("⚠️ Argumento --output ignorado para arquivos .spec (cada teste gera seu próprio arquivo).")
            success = process_spec(target_path, args.model)
        else:
            success = convert_file(target_path, args.model, output_path)
        if not success:
            sys.exit(1)

if __name__ == "__main__":
    main()

