# jurisprudencia-tjma-mcp

Servidor MCP para pesquisa de jurisprudência na API oficial do TJ-MA
(Jurisconsult — `apijuris.tjma.jus.br/v1`). Busca, leitura de decisões e
verificação de citações, no mesmo padrão do MCP-TJPI.

## Estado
- ✅ Transporte (Scrapling `Fetcher`, fingerprint TLS) + captcha resolvido pelo
  token JWT (sem OCR) + Bearer + hash de paginação `MD5(time+segredo)`.
- ⛔ **Pendente:** janela de paginação do `/infinito` ("Intervalo fora do
  permitido"). Finalizar com UMA requisição real do app (DevTools→Network).
- ⛔ `ler_decisao` / `verificar_citacao`: aguardam mapear o endpoint de inteiro teor.

## Rodar
```bash
uv sync
uv run jurisprudencia-tjma-mcp
```
