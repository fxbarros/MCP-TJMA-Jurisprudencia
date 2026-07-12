<h1 align="center">
    <img alt="MCP TJMA-Jurisprudência" src="https://raw.githubusercontent.com/fxbarros/MCP-TJMA-Jurisprudencia/main/docs/assets/banner.svg?sanitize=true">
    <br>
    <small>Jurisprudência do TJ-MA direto da API oficial — sem browser, sem scraping</small>
</h1>

<p align="center">
    <img alt="Python" src="https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white">
    <img alt="MCP" src="https://img.shields.io/badge/MCP-Claude%20Desktop-d97757">
    <img alt="httpx" src="https://img.shields.io/badge/transporte-httpx%20puro-black">
    <img alt="API oficial" src="https://img.shields.io/badge/fonte-API%20Jurisconsult%20(oficial)-8b0000">
</p>

<p align="center">
    <a href="#%EF%B8%8F-as-4-ferramentas"><strong>Ferramentas</strong></a>
    &middot;
    <a href="#%EF%B8%8F-como-funciona"><strong>Como funciona</strong></a>
    &middot;
    <a href="#-rodar"><strong>Rodar</strong></a>
    &middot;
    <a href="#-limita%C3%A7%C3%B5es"><strong>Limitações</strong></a>
</p>

Servidor [MCP](https://modelcontextprotocol.io) para pesquisa de jurisprudência na **API oficial do TJ-MA** (Jurisconsult — `apijuris.tjma.jus.br/v1`): busca, leitura de decisões e verificação literal de citações. Mesmo padrão do [MCP-TJPI](https://github.com/fxbarros/MCP-TJPI-Jurisprudencia), mas consumindo a **API JSON direto** — sem scraping de HTML, sem browser, sem OCR.

## ⚖️ As 4 ferramentas

| Ferramenta | O que faz |
|---|---|
| `buscar_jurisprudencia` | pesquisa acórdãos por palavra-chave. Filtros: `condicao` (E/OU/termo único), `classe` (id_classe) e `tipo_pesquisa` (Ementa, Inteiro Teor, Nº do Processo…) |
| `listar_filtros` | opções válidas de `classes` e `tipos_pesquisa` (id + label) |
| `ler_decisao` | lê a decisão pelo nº CNJ → metadados + inteiro teor + citação ABNT. Busca exata, nunca aproximada |
| `verificar_citacao` | confere se um trecho aparece **literalmente** no inteiro teor (anti-alucinação) |

## ⚙️ Como funciona

- **Transporte**: `httpx` puro (assíncrono, sem browser/OCR/solver).
- **Captcha de imagem**: resolvido decodificando o token JWT do `gera_captcha` — a resposta vem no próprio payload, sem OCR.
- **reCAPTCHA**: contornado com um `keyId` bogus — o backend pula a validação.
- **Busca**: endpoint `/sg/jurisprudencias/processos` com paginação `inicioPagina`/`fimPagina`.
- **Robustez**: retry com backoff no rate-limit por IP (403 transitório).

## 🚀 Rodar

```bash
uv sync
uv run jurisprudencia-tjma-mcp
```

Registro no Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "jurisprudencia-tjma": {
      "command": "uv",
      "args": ["--directory", "/caminho/para/jurisprudencia-tjma-mcp", "run", "jurisprudencia-tjma-mcp"]
    }
  }
}
```

Dependências: só `mcp[cli]` e `httpx` (Python ≥ 3.12).

## 🔍 Limitações

- Filtro por **câmara, relator, revisor, sistema e data** não está disponível nesta via (a API só os aplica no caminho validado por reCAPTCHA). Filtre por `classe` ou refine a `query`.
- A `query` **não** aceita operadores inline (`e`/`ou`/aspas são ignorados) — a lógica booleana é o parâmetro `condicao`.
- Depende de brechas do site; se o TJ-MA corrigir o captcha/reCAPTCHA, o acesso passaria a exigir um browser (semi-headless).

## ⚖️ Licença e autoria

Construído por [Fábio Ximenes Barros](https://github.com/fxbarros). Sem afiliação com o TJ-MA — usa apenas a API pública de jurisprudência.

<p align="center"><sub>Arte do banner: original — marca dos projetos MCP do autor.</sub></p>
