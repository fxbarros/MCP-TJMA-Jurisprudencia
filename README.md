# jurisprudencia-tjma-mcp

Servidor MCP para pesquisa de jurisprudência na **API oficial do TJ-MA**
(Jurisconsult — `apijuris.tjma.jus.br/v1`): busca, leitura de decisões e
verificação de citações. Mesmo padrão do MCP-TJPI, mas consumindo a API JSON
direto (sem scraping de HTML, sem browser).

## Tools

| Tool | O que faz |
|------|-----------|
| `buscar_jurisprudencia` | Pesquisa acórdãos por palavra-chave. Filtros: `condicao` (E/OU/termo único), `classe` (id_classe) e `tipo_pesquisa` (Ementa, Inteiro Teor, Nº do Processo, etc.). |
| `listar_filtros` | Lista as opções válidas de `classes` e `tipos_pesquisa` (id + label). |
| `ler_decisao` | Lê uma decisão pelo número CNJ e devolve metadados + inteiro teor + citação ABNT. Busca exata; não devolve decisão aproximada. |
| `verificar_citacao` | Confere se um trecho aparece literalmente no inteiro teor (anti-alucinação). |

## Como funciona

- **Transporte:** `httpx` puro (assíncrono, sem browser/OCR/solver).
- **Captcha de imagem:** resolvido decodificando o token JWT do `gera_captcha`
  (a resposta vem no payload; sem OCR).
- **reCAPTCHA:** contornado com um `keyId` bogus — o backend pula a validação.
- **Busca:** endpoint `/sg/jurisprudencias/processos` com paginação
  `inicioPagina`/`fimPagina`.
- **Robustez:** retry/backoff no rate-limit por IP (403 transitório).

## Limitações

- Filtro por **câmara, relator, revisor, sistema e data** não está disponível
  nesta via (a API só os aplica no caminho validado por reCAPTCHA). Filtre por
  `classe` ou refine a `query`.
- A `query` **não** aceita operadores inline (`e`/`ou`/aspas são ignorados) —
  a lógica booleana é o parâmetro `condicao`.
- Depende de brechas do site; se o TJ-MA corrigir o captcha/reCAPTCHA, o acesso
  passaria a exigir um browser (via semi-headless).

## Rodar

```bash
uv sync
uv run jurisprudencia-tjma-mcp
```

Configuração no Claude Desktop (`claude_desktop_config.json`):

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

## Dependências

Só `mcp[cli]` e `httpx` (ver `pyproject.toml`). Requer Python ≥ 3.12.
