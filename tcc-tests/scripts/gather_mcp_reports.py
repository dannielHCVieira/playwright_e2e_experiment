#!/usr/bin/env python3
"""
Utility script used by the MCP workflow to collect JSON reports from
downloaded artifacts and copy them into a flat folder that the report
generator can consume.
"""

import argparse
import shutil
from pathlib import Path


def slug_from_path(path: Path) -> str:
    """Extract the artifact slug from the path (e.g., mcp-todomvc-artifacts)."""
    for part in path.parts:
        if part.startswith("mcp-") and part.endswith("-artifacts"):
            return part
    return "report"


def collect_reports(source_dir: Path, dest_dir: Path) -> None:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not source_dir.exists():
        print(f"Diretório {source_dir} não encontrado; nenhuma coleta realizada.")
        return

    found = False
    for json_file in source_dir.rglob("mcp-report*.json"):
        if "reports" not in json_file.parts:
            continue
        slug = slug_from_path(json_file)
        target = dest_dir / f"{slug}__{json_file.name}"
        shutil.copy2(json_file, target)
        print(f"Copiado {json_file} -> {target}")
        found = True

    if not found:
        print("Nenhum arquivo mcp-report*.json encontrado em all-artifacts.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect MCP JSON reports from artifacts.")
    parser.add_argument("--source", required=True, help="Path to the root folder containing artifacts.")
    parser.add_argument("--dest", required=True, help="Path to the folder where JSON files will be copied.")
    args = parser.parse_args()

    collect_reports(Path(args.source), Path(args.dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

