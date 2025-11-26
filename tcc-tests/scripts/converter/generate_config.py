import argparse
import sys
import re
import json
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

class ConfigGenerator:
    def __init__(self, project_name: str, base_url: Optional[str] = None):
        self.project_name = project_name
        
        # Detecção do diretório base para execução flexível
        current_dir = Path.cwd()
        
        # Primeiro verifica se está DENTRO de tcc-tests (tem tests/example)
        if (current_dir / "tests" / "example").exists():
            # Executando de dentro de tcc-tests/
            self.base_path = Path("tests/example") / project_name
            self.examples_root = current_dir.parent / "examples"
        # Depois verifica se está na raiz do repo (tem tcc-tests/)
        elif (current_dir / "tcc-tests").exists():
            # Executando da raiz do repositório
            self.base_path = Path("tcc-tests/tests/example") / project_name
            self.examples_root = Path("examples")
        else:
            # Fallback - assume que está em tcc-tests
            self.base_path = Path("tests/example") / project_name
            self.examples_root = current_dir.parent / "examples"

        self.nl_path = self.base_path / "natural_language"
        self.config_path = self.base_path / "config"
        self.base_url = base_url or self._extract_url_from_project()

    def _strip_line_comments(self, content: str) -> str:
        sanitized_lines = []
        for line in content.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("//"):
                sanitized_lines.append("")
                continue
            if "://" in line:
                sanitized_lines.append(line)
            else:
                sanitized_lines.append(line.split("//", 1)[0])
        return "\n".join(sanitized_lines)

    def _extract_url_from_project(self) -> str:
        """
        Tenta extrair a URL base de fixtures ou config do projeto original.
        """
        original_project_path = self.examples_root / self.project_name
        
        goto_abs_pattern = re.compile(r"page\.goto\(\s*['\"](https?://[^'\"]+)['\"]")
        goto_rel_pattern = re.compile(r"page\.goto\(\s*['\"](/[^'\"]*)['\"]")
        baseurl_pattern = re.compile(r"baseURL\s*[:=]\s*['\"]([^'\"]+)['\"]")
        webserver_port_pattern = re.compile(r"webServer\s*:\s*{[^}]*port\s*[:=]\s*(\d+)", re.DOTALL)

        # 1) Procura URLs absolutas diretamente nos testes (fixtures antes para priorizar setup)
        tests_dir = original_project_path / "tests"
        relative_path = None
        if tests_dir.exists():
            for file_path in sorted(tests_dir.rglob("*.*")):
                if file_path.suffix not in {".ts", ".js"}:
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8")
                    match_abs = goto_abs_pattern.search(content)
                    if match_abs:
                        url = match_abs.group(1)
                        print(f"📍 URL encontrada em {file_path.name}: {url}")
                        return url
                    if relative_path is None:
                        match_rel = goto_rel_pattern.search(content)
                        if match_rel:
                            relative_path = match_rel.group(1)
                except Exception:
                    continue

        # 2) Procura baseURL/webServer no config
        config_candidates = [
            original_project_path / "playwright.config.ts",
            original_project_path / "playwright.config.js",
        ]
        base_host = None
        base_url_candidate = None

        for cfg_path in config_candidates:
            if not cfg_path.exists():
                continue
            try:
                content = cfg_path.read_text(encoding="utf-8")
            except Exception:
                continue

            sanitized = self._strip_line_comments(content)

            match_base = baseurl_pattern.search(sanitized)
            if match_base and not base_url_candidate:
                candidate = match_base.group(1)
                if candidate.startswith("http"):
                    print(f"📍 URL encontrada em {cfg_path.name}: {candidate}")
                    return candidate
                base_url_candidate = candidate

            match_web = webserver_port_pattern.search(sanitized)
            if match_web and not base_host:
                port = match_web.group(1)
                base_host = f"http://localhost:{port}"

        # 3) Se baseURL relativo (ex: "/"), combina com host detectado
        # 3) Procura baseURL/test.use dentro dos próprios testes
        tests_baseurl_candidate = None
        if tests_dir.exists():
            for file_path in sorted(tests_dir.rglob("*.*")):
                if file_path.suffix not in {".ts", ".js"}:
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8")
                except Exception:
                    continue
                sanitized = self._strip_line_comments(content)
                match_base = baseurl_pattern.search(sanitized)
                if match_base:
                    candidate = match_base.group(1)
                    if candidate.startswith("http"):
                        print(f"📍 URL encontrada em {file_path.name}: {candidate}")
                        return candidate
                    tests_baseurl_candidate = tests_baseurl_candidate or candidate

        # Combinações prioritárias
        if base_host and base_url_candidate:
            combined = urljoin(base_host, base_url_candidate)
            print(f"📍 URL combinada (webServer + baseURL): {combined}")
            return combined

        if base_host and tests_baseurl_candidate:
            combined = urljoin(base_host, tests_baseurl_candidate)
            print(f"📍 URL combinada (test.use + webServer): {combined}")
            return combined

        if tests_baseurl_candidate:
            if tests_baseurl_candidate.startswith("http"):
                print(f"📍 URL encontrada (test.use): {tests_baseurl_candidate}")
                return tests_baseurl_candidate
            if base_host:
                combined = urljoin(base_host, tests_baseurl_candidate)
                print(f"📍 URL combinada (test.use + host): {combined}")
                return combined

        if base_host:
            print(f"📍 URL derivada do webServer: {base_host}")
            if relative_path:
                combined = urljoin(base_host, relative_path)
                print(f"📍 URL expandida com caminho relativo: {combined}")
                return combined
            return base_host

        if relative_path:
            print(f"📍 Caminho relativo encontrado: {relative_path}")
            return relative_path

        if base_url_candidate:
            print(f"📍 Caminho base encontrado no config: {base_url_candidate}")
            return base_url_candidate

        print("⚠️ URL não encontrada automaticamente. Usando placeholder.")
        return "http://LOCALHOST_OR_PLACEHOLDER"

    def _parse_markdown_steps(self, md_path: Path) -> List[str]:
        """
        Lê o arquivo MD e extrai os bullet points como lista de strings.
        """
        steps = []
        try:
            content = md_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                # Remove marcadores de lista (- ou *)
                if line.startswith(("- ", "* ")):
                    steps.append(line[2:].strip())
                # Remove marcadores numéricos (1. )
                elif re.match(r"^\d+\.\s", line):
                    parts = line.split(".", 1)
                    if len(parts) > 1:
                        steps.append(parts[1].strip())
        except Exception as e:
            print(f"❌ Erro ao ler {md_path}: {e}")
        return steps

    def _update_yaml(self, new_entries: List[Dict[str, Any]]):
        yaml_file = self.config_path / "prompt.yml"
        existing_data = []
        
        if yaml_file.exists():
            try:
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    existing_data = yaml.safe_load(f) or []
            except Exception as e:
                print(f"⚠️ Erro ao ler YAML existente: {e}")

        # Cria um mapa para atualização fácil por nome
        data_map = {item.get("name"): item for item in existing_data}
        
        for entry in new_entries:
            data_map[entry["name"]] = entry
            
        updated_list = list(data_map.values())
        
        with open(yaml_file, 'w', encoding='utf-8') as f:
            yaml.dump(updated_list, f, allow_unicode=True, sort_keys=False, indent=2)
        print(f"💾 YAML atualizado: {yaml_file}")

    def _update_json(self, new_entries: List[Dict[str, Any]]):
        json_file = self.config_path / "prompt.json"
        existing_data = []
        
        if json_file.exists():
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f) or []
            except Exception as e:
                print(f"⚠️ Erro ao ler JSON existente: {e}")

        data_map = {item.get("name"): item for item in existing_data}
        
        for entry in new_entries:
            data_map[entry["name"]] = entry
            
        updated_list = list(data_map.values())
        
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(updated_list, f, ensure_ascii=False, indent=2)
        print(f"💾 JSON atualizado: {json_file}")

    def run(self):
        if not self.nl_path.exists():
            print(f"❌ Diretório não encontrado: {self.nl_path}")
            return

        self.config_path.mkdir(parents=True, exist_ok=True)
        
        new_entries = []
        md_files = list(self.nl_path.glob("*.md"))
        
        print(f"🔍 Encontrados {len(md_files)} arquivos de linguagem natural.")
        
        for md_file in md_files:
            # O nome do teste é o nome do arquivo sem sufixos extras se houver
            # Remove _natural_language do nome se existir para ficar mais limpo
            test_name = md_file.stem.replace("_natural_language", "").replace(".spec", "")
            steps = self._parse_markdown_steps(md_file)
            
            if not steps:
                print(f"⚠️ Nenhum passo extraído de {md_file.name}")
                continue
                
            entry = {
                "name": test_name,
                "url": self.base_url,
                "prompt": steps,
                "expected": True # Default value, requires manual check later
            }
            new_entries.append(entry)

        if new_entries:
            self._update_yaml(new_entries)
            self._update_json(new_entries)
        else:
            print("⚠️ Nenhuma entrada nova gerada.")

def main():
    parser = argparse.ArgumentParser(description='Gera arquivos de configuração (YAML/JSON) a partir de testes em linguagem natural.')
    parser.add_argument('project', type=str, help='Nome do projeto (ex: todomvc)')
    parser.add_argument('--base-url', type=str, help='URL base para os testes (opcional, tenta extrair auto)', required=False)

    args = parser.parse_args()

    generator = ConfigGenerator(args.project, args.base_url)
    generator.run()

if __name__ == "__main__":
    main()

