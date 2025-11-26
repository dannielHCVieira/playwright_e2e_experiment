from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from .schemas import ConversionConfig

class TestConverter:
    def __init__(self, config: ConversionConfig):
        self.config = config
        self.llm = ChatOpenAI(model=self.config.model_name, temperature=0)

    def _get_prompt_template(self) -> ChatPromptTemplate:
        template = """
        Você é um especialista em automação de testes e engenharia de prompt para agentes de IA.
        
        TESTE ALVO:
        - Nome: {test_label}
        - Contexto: {test_context}
        
        OBJETIVO:
        Analise o código de teste E2E (Playwright/TypeScript) fornecido e converta-o em uma LISTA DE PASSOS em linguagem natural.
        
        IMPORTANTE:
        - A saída deve ser APENAS uma lista markdown com bullet points (- ).
        - NÃO inclua introduções, títulos ou conclusões.
        - Cada bullet point será um passo executável pelo agente.

        INSTRUÇÕES DE CONTEÚDO:
        1. Use verbos imperativos (Clique, Digite, Valide).
        2. Seja específico com textos e dados (ex: "Digite 'teste@email.com' no campo email").
        3. Para validações, use "Verifique se...".
        4. Quando uma digitação for seguida de pressionar Enter, COMBINE em um único passo:
           - CORRETO: "Digite 'texto' no campo 'X' e pressione Enter"
           - ERRADO: "Digite 'texto' no campo 'X'" seguido de "Pressione 'Enter'"
        5. Ao mencionar Enter, use apenas "pressione Enter" (sem aspas, sem especificar tecla ou campo).
        
        EXEMPLO DE SAÍDA ESPERADA:
        - Digite 'admin' no campo de usuário e pressione Enter
        - Clique no botão 'Entrar'
        - Verifique se o texto 'Bem-vindo' está visível

        CÓDIGO DO TESTE:
        {code}

        LISTA DE PASSOS (Markdown):
        """
        return ChatPromptTemplate.from_template(template)

    def run(self):
        if self.config.code_snippet:
            code_content = self.config.code_snippet
        else:
            with open(self.config.input_file, 'r', encoding='utf-8') as f:
                code_content = f.read()

        label = self.config.test_full_name or self.config.test_name or self.config.input_file.stem
        context = self.config.test_full_name or self.config.test_name or "Não informado"

        print(f"🤖 Processando '{label}' usando {self.config.model_name}...")

        # 2. Configurar a Chain
        prompt = self._get_prompt_template()
        chain = prompt | self.llm | StrOutputParser()

        # 3. Executar
        try:
            result = chain.invoke(
                {
                    "code": code_content,
                    "test_label": label,
                    "test_context": context,
                }
            )
        except Exception as e:
            raise RuntimeError(f"Falha na execução da LLM: {e}")

        # 4. Salvar resultado
        output_file = self.config.final_output_path
        
        # Garante que o diretório pai existe (caso não tenha sido criado pelo schema)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result)
            
        print(f"✅ Sucesso! Prompt salvo em: {output_file}")
