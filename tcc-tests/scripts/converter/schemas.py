from pathlib import Path
from typing import Optional
import re
from pydantic import BaseModel, Field, FilePath, computed_field

class ConversionConfig(BaseModel):
    """
    Configuração para o processo de conversão de código para linguagem natural.
    """
    input_file: FilePath = Field(..., description="Caminho para o arquivo .spec.ts de entrada")
    output_path: Optional[Path] = Field(None, description="Caminho opcional para o arquivo de saída")
    model_name: str = Field("gpt-5-mini", description="Nome do modelo da OpenAI a ser utilizado")
    test_name: Optional[str] = Field(None, description="Nome individual do teste (quando múltiplos por arquivo)")
    test_full_name: Optional[str] = Field(None, description="Nome completo incluindo describes")
    test_slug: Optional[str] = Field(None, description="Slug único para o teste")
    code_snippet: Optional[str] = Field(None, description="Trecho de código específico do teste")

    @computed_field
    def final_output_path(self) -> Path:
        """
        Calcula o caminho de saída absoluto seguindo:
        <repo>/tcc-tests/tests/example/<PROJETO>/natural_language/
        """
        if self.output_path:
            return self.output_path

        input_path = Path(self.input_file).resolve()
        parts = input_path.parts

        project_name = "unknown"
        repo_root: Optional[Path] = None

        if "examples" in parts:
            idx = parts.index("examples")
            if idx + 1 < len(parts):
                project_name = parts[idx + 1]
            repo_root = Path(*parts[:idx]) if idx > 0 else None

        if repo_root is None or not repo_root.exists():
            # Fallback: assume que este arquivo está em <repo>/tcc-tests/scripts
            repo_root = Path(__file__).resolve().parents[2]

        tests_base = repo_root / "tcc-tests" / "tests" / "example"
        target_dir = tests_base / project_name / "natural_language"
        target_dir.mkdir(parents=True, exist_ok=True)

        slug_suffix = f"__{self.test_slug}" if self.test_slug else ""
        filename = f"{input_path.stem}{slug_suffix}_natural_language.md"
        return target_dir / filename
