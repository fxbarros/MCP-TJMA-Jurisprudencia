"""Servidor MCP para a API de jurisprudência do TJ-MA (Jurisconsult).

Expõe três tools ao Claude Desktop:
- buscar_jurisprudencia: pesquisa por palavra-chave
- ler_decisao: extrai metadados completos de uma decisão (a implementar)
- verificar_citacao: confere se um trecho aparece literalmente no inteiro teor
"""
from __future__ import annotations

import asyncio
from typing import Optional

from mcp.server.fastmcp import FastMCP

from jurisprudencia_tjma_mcp.tjma_client import (
    TJMAClient,
    ConsultaRecusada,
)


def _erro(msg: str) -> dict:
    return {
        "erro": (
            f"Falha ao consultar a API do TJ-MA: {msg}. "
            "O site pode estar lento, com o IP em rate-limit, ou a janela de "
            "paginação recusou a requisição. NÃO invente resultados — avise o "
            "usuário e sugira tentar novamente em instantes."
        ),
    }


mcp = FastMCP("jurisprudencia-tjma")

_client: Optional[TJMAClient] = None
_client_lock = asyncio.Lock()


async def _get_client() -> TJMAClient:
    global _client
    async with _client_lock:
        if _client is None:
            _client = TJMAClient()
        return _client


@mcp.tool()
async def buscar_jurisprudencia(
    query: str,
    limite: int = 10,
    page: int = 1,
    condicao: Optional[str] = None,
    classe: Optional[str] = None,
    tipo_pesquisa: Optional[str] = None,
) -> dict:
    """Pesquisa jurisprudência (acórdãos) do TJ-MA por palavra-chave.

    Consulta a API oficial do Jurisconsult (apijuris.tjma.jus.br). Por padrão
    pesquisa `query` na EMENTA, tratando-a como termo único.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    SINTAXE DA QUERY — SEM operadores inline
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    NÃO use "e"/"ou"/"nao"/aspas dentro da `query` — a API os ignora (não são
    operadores como no TJ-PI). A lógica booleana entre palavras é escolhida
    pelo parâmetro `condicao`:
      • condicao="termo" (default) → a query como termo/expressão única
      • condicao="e"  → todas as palavras devem aparecer (AND)
      • condicao="ou" → qualquer uma das palavras (OR; busca mais ampla)
    Ex.: para "dano" E "moral", use query="dano moral", condicao="e".

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ANTI-ALUCINAÇÃO — uso do campo `ementa`
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    A `ementa` já vem do texto do acórdão (não é preview do servidor), mas
    para CITAR trechos em peça use `ler_decisao(numero_cnj)` + `verificar_citacao`
    e cite a partir do `inteiro_teor`.

    Args:
        query: termos de busca. Onde são pesquisados depende de `tipo_pesquisa`
               (por padrão, na ementa). Sem operadores — veja `condicao`.
        limite: número máximo de resultados (1-50). Default: 10.
        page: página inicial. Default: 1.
        condicao: como combinar as palavras da query: "termo" (default, termo
                único), "e" (AND) ou "ou" (OR).
        classe: id_classe para restringir à classe judicial (ex.: só Apelações).
                Use `listar_filtros("classes")` para achar o id.
        tipo_pesquisa: campo onde `query` é pesquisada. Default 1=Ementa.
                Opções: 1=Ementa, 7=Inteiro Teor, 2=Advogado(s), 3=Classe,
                4=Número do Acórdão, 6=Número do Processo. Ex.: use 6 com um
                número CNJ como `query` para achar uma decisão específica.

    (Filtro por câmara, relator, revisor e intervalo de datas NÃO está
    disponível nesta via — a API só os aplica no caminho validado por
    reCAPTCHA. Filtre por classe ou refine a `query`.)

    Returns:
        Dict com resultados (titulo, numero_cnj, tipo_decisao [espécie/classe],
        assunto [comarca], publicacao, ementa, url [= o CNJ, usado em
        ler_decisao]), total_retornado, total_no_servidor [total REAL da query
        no acervo — use este para afirmar quantidades], query_executada, page,
        limite. Campo `erro` em caso de falha.
    """
    limite = max(1, min(limite, 50))
    filtros = {"classe": classe, "tipo_pesquisa": tipo_pesquisa,
               "condicao": condicao}
    client = await _get_client()
    try:
        busca = await client.buscar_jurisprudencia(
            query=query, limite=limite, page=page, filtros=filtros,
        )
    except (ConsultaRecusada, ValueError) as e:
        return _erro(str(e))
    except Exception as e:
        return _erro(f"{type(e).__name__}: {e}")
    itens = [r.to_dict() for r in busca.resultados]
    resposta: dict = {
        "resultados": itens,
        "total_retornado": len(itens),
        "total_no_servidor": busca.total_no_servidor,
        "query_executada": query,
        "page": page,
        "limite": limite,
    }
    if busca.duplicatas_removidas:
        resposta["duplicatas_removidas"] = busca.duplicatas_removidas
    if len(itens) == 0:
        resposta["_aviso"] = (
            "ZERO RESULTADOS. Reformule a query (sinônimos, menos termos, sem "
            "aspas) antes de relatar 'nada encontrado'."
        )
    return resposta


@mcp.tool()
async def listar_filtros(tipo: str) -> dict:
    """Lista as opções válidas de um filtro de buscar_jurisprudencia.

    Chame ANTES de usar `classe`, para descobrir o `id` correto. Os valores
    mudam de tribunal e não são adivinháveis.

    Args:
        tipo: "classes" ou "tipos_pesquisa".
              - classes         -> id = id_classe (para o parâmetro `classe`)
              - tipos_pesquisa  -> id da opção (para `tipo_pesquisa`; 1=Ementa)

    Returns:
        Dict com `tipo`, `total` e `opcoes` (lista de {id, label}). Filtre a
        lista você mesmo pelo nome que o usuário mencionou e use o `id`.
    """
    client = await _get_client()
    try:
        opcoes = await client.listar_filtros(tipo)
    except ValueError as e:
        return _erro(str(e))
    except Exception as e:
        return _erro(f"{type(e).__name__}: {e}")
    return {"tipo": tipo, "total": len(opcoes), "opcoes": opcoes}


@mcp.tool()
async def ler_decisao(url_or_id: str) -> dict:
    """Lê uma decisão individual do TJ-MA e extrai os metadados formais.

    Use SEMPRE antes de citar uma decisão em peça processual. O `inteiro_teor`
    é a fonte confiável para citações diretas.

    Args:
        url_or_id: número CNJ da decisão (ex.: 0002186-95.2015.8.10.0040),
                   obtido no campo `numero_cnj`/`url` de buscar_jurisprudencia.

    Returns:
        Dict com metadados (relator, orgao, comarca, sistema, publicacao,
        classe) + ementa + inteiro_teor + citacao_abnt.
    """
    client = await _get_client()
    try:
        d = await client.ler_decisao(url_or_id)
    except NotImplementedError as e:
        return _erro(str(e))
    except Exception as e:
        return _erro(f"{type(e).__name__}: {e}")
    out = d.to_dict()
    out["citacao_abnt"] = d.citacao_abnt()
    return out


@mcp.tool()
async def verificar_citacao(url_or_id: str, trecho: str) -> dict:
    """Confere se um trecho aparece literalmente no inteiro_teor de uma decisão.

    ⚠️ USE SEMPRE antes de incluir citação direta entre aspas. Se `valido:
    False`, NÃO cite — reescreva como paráfrase. Comparação tolerante a
    acentos, caixa e espaços; intolerante a substituição/omissão de palavras.

    Args:
        url_or_id: número CNJ da decisão (mesmo formato de ler_decisao).
        trecho: texto que se pretende citar entre aspas.

    Returns:
        Dict com valido (bool), motivo (str), url (str|None).
    """
    client = await _get_client()
    try:
        return await client.verificar_citacao(url_or_id, trecho)
    except NotImplementedError as e:
        return _erro(str(e))
    except Exception as e:
        return _erro(f"{type(e).__name__}: {e}")


def main() -> None:
    """Entry point pro `uv run jurisprudencia-tjma-mcp`."""
    mcp.run()


if __name__ == "__main__":
    main()
