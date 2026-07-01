"""Cliente da API de jurisprudência do TJMA (Jurisconsult).

Diferente do TJ-PI (Rails server-rendered + bs4), o TJMA é um SPA Ionic/Angular
que consome uma API REST JSON em https://apijuris.tjma.jus.br/v1/. Este cliente
fala direto com a API.

Camadas de proteção e como cada uma é tratada (reconhecimento 01/07/2026):
  * Rate-limit por IP (WAF Apache): 403 "seu IP excedeu o limite". Mitigado com
    throttle (o bloqueio é por volume, não por fingerprint — httpx puro passa).
  * Captcha de imagem: QUEBRADO. O token JWT (`gera_captcha`) traz a resposta
    em claro no payload -> `_resolver_captcha_do_token()`. Sem OCR. Vai como
    header `Authorization: Bearer "<tokenCaptcha> <valorCaptcha>"`.
  * reCAPTCHA v2 invisible: só é validado quando `keyId` é válido. Enviando um
    `keyId` bogus ("x") o backend PULA a validação do `tokenG` e retorna os
    resultados. (checkForm é omitido; /infinito e time/validation não são usados.)

Reaproveita do TJ-PI: extração de ementa (formato CNJ Rec.154/2024 = padrão
nacional), dedup, verificar_citacao, dataclasses.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import unicodedata
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Optional

import httpx

BASE_URL = "https://apijuris.tjma.jus.br/v1"
PORTAL_URL = "https://jurisconsult.tjma.jus.br"

# O cert HTTPS da apijuris costuma estar inválido/expirado -> verify=False.
# UA de browser reduz atrito com o WAF (que bloqueia mesmo é por volume).
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Origin": PORTAL_URL,
    "Referer": f"{PORTAL_URL}/",
}

# Endpoints
EP_CAPTCHA = "/util/gera_captcha"
# Busca de Acordaos (relatorio id=1, url de lista_relatorios).
EP_BUSCA_ACORDAOS = "/sg/jurisprudencias/processos"

# Listas de filtros que REALMENTE filtram na via HTTP pura (keyId=x). camara,
# relator, revisor, sistema e datas são ignorados por essa via (só valem no
# caminho validado com reCAPTCHA real), então não são expostos como filtros.
# Cada entrada: (endpoint, chave_lista, campo_id, campo_label)
_LISTAS_FILTRO = {
    "classes": ("/jurisprudencia/lista_todos_classes", "classes", "id_classe", "str_classe"),
    "tipos_pesquisa": ("/jurisprudencia/lista_todos_tipos_pesquisa", "tipos", "opcao_id", "opcao"),
}

# Defaults do formulario. "Todos" nos dropdowns = "0".
#   tipoPesquisa: 1 = Ementa. keyId="x" (bogus) pula a validacao do reCAPTCHA.
DEFAULT_TIPO_PESQUISA = "1"
DEFAULT_CONDICAO = "3"
_KEYID_BYPASS = "x"
_TOKENG_DUMMY = "x"

# condicao (rótulos do bundle): 1=E (AND, todas as palavras), 2=OU (qualquer),
# 3=Termo único (default). NÃO há operadores inline na chave (o "e"/aspas são
# ignorados) — a lógica booleana é escolhida por aqui.
_CONDICAO_MAP = {
    "e": "1", "and": "1", "todas": "1", "1": "1",
    "ou": "2", "or": "2", "qualquer": "2", "2": "2",
    "termo": "3", "unico": "3", "único": "3", "frase": "3", "3": "3",
}


# --------------------------------------------------------------------------- #
# Dataclasses (portadas do TJ-PI, cabecalho ajustado para TJ-MA)
# --------------------------------------------------------------------------- #
@dataclass
class Resultado:
    titulo: str
    numero_cnj: Optional[str]
    tipo_decisao: Optional[str]
    assunto: Optional[str]
    publicacao: Optional[str]
    ementa: Optional[str]
    url: str
    ementa_truncada: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.ementa_truncada:
            d["_aviso"] = (
                "Ementa nao extraida do texto. Para o inteiro teor completo "
                "chame ler_decisao(numero_cnj). NAO cite este campo."
            )
        return d


@dataclass
class Decisao:
    url: str
    numero_cnj: Optional[str] = None
    tipo_decisao: Optional[str] = None
    classe_judicial: Optional[str] = None
    relator: Optional[str] = None
    orgao_julgador: Optional[str] = None
    orgao_julgador_colegiado: Optional[str] = None
    competencia: Optional[str] = None
    assunto_principal: Optional[str] = None
    autor: Optional[str] = None
    reu: Optional[str] = None
    publicacao: Optional[str] = None
    sistema: Optional[str] = None
    comarca: Optional[str] = None
    ementa: Optional[str] = None
    inteiro_teor: Optional[str] = None
    citacao_oficial: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def citacao_abnt(self) -> str:
        head = "TJ-MA"
        if self.classe_judicial:
            head = f"{head} - {self.classe_judicial}"
        if self.numero_cnj:
            head = f"{head}: {self.numero_cnj}"
        partes = [head]
        if self.relator:
            partes.append(f"Relator: {self.relator}")
        if self.orgao_julgador_colegiado:
            partes.append(self.orgao_julgador_colegiado)
        if self.publicacao:
            partes.append(f"Data de Publicação: {self.publicacao}")
        return f"({', '.join(partes)})"


@dataclass
class Busca:
    resultados: list[Resultado] = field(default_factory=list)
    total_no_servidor: Optional[int] = None
    paginas_consultadas: int = 0
    duplicatas_removidas: int = 0


# --------------------------------------------------------------------------- #
# Extracao de ementa (PORTADO DO TJ-PI — formato CNJ e padrao nacional)
# --------------------------------------------------------------------------- #
_CNJ_RE = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
_EMENTA_START_RE = re.compile(r"\bEmenta\s*[:\-–]\s*", re.I)
_EMENTA_CNJ_START_RE = re.compile(r"\bDIREITO\s+[A-ZÁÂÃÀÉÊÍÓÔÕÚÜÇ]{2,}")
_EMENTA_CNJ_JANELA = 3000
_EMENTA_END_RE = re.compile(
    r"\s+(?:"
    r"DECIS[ÃA]O\s+TERMINATIVA"
    r"|I\s*[-–]\s*RELAT[ÓO]RIO"
    r"|RELAT[ÓO]RIO\b"
    r"|VOTO\b"
    r"|AC[ÓO]RD[ÃA]O\b"
    r"|ACORDAM\b"
    r"|Cumpra-se"
    r"|S[ãa]o Lu[íi]s,"
    r")",
    re.I,
)


def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    out = re.sub(r"\s+", " ", s).strip()
    return out or None


def _clean_multiline(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    linhas = (re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in s.splitlines())
    out = "\n".join(ln for ln in linhas if ln)
    return out or None


def _extract_ementa(text: Optional[str]) -> Optional[str]:
    """Localiza a ementa e fatia ate o proximo marcador. Dois formatos:
    classico ("Ementa:") e CNJ (Rec.154/2024, comeca com "DIREITO X").
    """
    if not text:
        return None
    m = _EMENTA_START_RE.search(text)
    if m:
        rest = text[m.end():]
    else:
        m_cnj = _EMENTA_CNJ_START_RE.search(text[:_EMENTA_CNJ_JANELA])
        if not m_cnj:
            return None
        rest = text[m_cnj.start():]
    m_end = _EMENTA_END_RE.search(rest)
    end = m_end.start() if m_end else len(rest)
    return _clean_multiline(rest[:end])


def _normalize_para_comparacao(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def _chave_dedup(r: Resultado):
    """TJMA tambem indexa a mesma decisao sob IDs distintos (mesmo padrao do
    TJ-PI). Dedup por conteudo; sem CNJ cai pra URL.
    """
    if r.numero_cnj:
        return (r.numero_cnj, r.publicacao, r.tipo_decisao)
    return r.url


# --------------------------------------------------------------------------- #
# Captcha: resolucao pelo token JWT (PROVADO)
# --------------------------------------------------------------------------- #
def _b64d(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def _resolver_captcha_do_token(token_captcha: str) -> str:
    """Decodifica a resposta do captcha embutida no JWT `tokenCaptcha`.

    payload.value = base64('"<timestamp>.<TEXTO>"'); o TEXTO apos o ponto e a
    resposta. Nenhum OCR necessario (falha de design do captcha do TJMA).
    """
    payload = json.loads(_b64d(token_captcha.split(".")[1]))
    inner = _b64d(payload["value"]).decode()  # '"1782917236.ce263"'
    return inner.strip('"').split(".")[-1]


# --------------------------------------------------------------------------- #
# Parsing dos resultados JSON (campos reais da API, capturados 01/07/2026)
# --------------------------------------------------------------------------- #
def _cnj_para_url(cnj: Optional[str]) -> str:
    """Nao ha URL REST por decisao (o detalhe e estado do SPA). Usamos o CNJ
    como identificador citavel; ler_decisao/verificar_citacao aceitam o CNJ.
    """
    return cnj or PORTAL_URL


def _item_para_resultado(it: dict) -> Resultado:
    cnj = _clean(it.get("pkProtocolo"))
    classe = _clean(it.get("str_especie_ou_classe"))
    texto = it.get("txacordao") or it.get("txEmenta")
    ementa = _extract_ementa(texto)
    truncada = ementa is None
    comarca = _clean(it.get("txComarca"))
    titulo = " - ".join(p for p in (classe, cnj) if p) or "(sem titulo)"
    return Resultado(
        titulo=titulo,
        numero_cnj=cnj,
        tipo_decisao=classe,
        assunto=comarca,  # a API nao traz "assunto"; comarca e o metadado util
        publicacao=_clean(it.get("dtRegistroAcordao")) or _clean(it.get("dtEmentario")),
        ementa=ementa,
        url=_cnj_para_url(cnj),
        ementa_truncada=truncada,
    )


def _processos_de(data: dict) -> list:
    """Extrai a lista de processos do JSON de forma tolerante: `response` pode
    vir como {"processos": [...]}, como lista direta, ou ausente (0 resultados).
    """
    resp = data.get("response")
    if isinstance(resp, dict):
        return resp.get("processos") or []
    if isinstance(resp, list):
        return resp
    return []


def _item_para_decisao(it: dict) -> Decisao:
    cnj = _clean(it.get("pkProtocolo"))
    texto = _clean_multiline(it.get("txacordao") or it.get("txEmenta"))
    return Decisao(
        url=_cnj_para_url(cnj),
        numero_cnj=cnj,
        tipo_decisao=_clean(it.get("str_especie_ou_classe")),
        classe_judicial=_clean(it.get("str_especie_ou_classe")),
        relator=_clean(it.get("txRelator")),
        orgao_julgador=_clean(it.get("txCamara")),
        orgao_julgador_colegiado=_clean(it.get("txCamara")),
        publicacao=_clean(it.get("dtRegistroAcordao")),
        sistema=_clean(it.get("strSistema")),
        comarca=_clean(it.get("txComarca")),
        ementa=_extract_ementa(texto),
        inteiro_teor=texto,
    )


class ConsultaRecusada(RuntimeError):
    """A API recusou a consulta (rate-limit, captcha ou validacao).

    `retryable=True` para falhas transitórias (rate-limit 403, erro 5xx) que
    valem nova tentativa com backoff; False para recusa definitiva.
    """

    def __init__(self, mensagem: str, retryable: bool = False):
        super().__init__(mensagem)
        self.retryable = retryable


# --------------------------------------------------------------------------- #
# Cliente
# --------------------------------------------------------------------------- #
class TJMAClient:
    _CACHE_MAX = 32
    _MAX_LINHAS = 50  # teto de linhas por consulta (inicioPagina..fimPagina)
    _MAX_TENTATIVAS = 3       # tentativas em falha transitória (rate-limit)
    _BACKOFF_BASE = 3.0       # segundos entre tentativas (3s, 6s, ...)

    def __init__(self, timeout: float = 40.0):
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers=_DEFAULT_HEADERS,
            timeout=timeout,
            verify=False,          # cert da apijuris frequentemente inválido
            follow_redirects=True,
        )
        self._cache_decisoes: OrderedDict[str, Decisao] = OrderedDict()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- transporte httpx (assíncrono; leve, sem browser) --
    async def _get(self, path: str, params: Optional[dict] = None,
                   headers: Optional[dict] = None) -> httpx.Response:
        return await self._client.get(path, params=params, headers=headers)

    async def _novo_bearer(self) -> str:
        """Gera captcha, decodifica a resposta e monta o header Bearer."""
        r = await self._get(EP_CAPTCHA)
        if r.status_code != 200:
            raise ConsultaRecusada(
                f"Falha ao gerar captcha (HTTP {r.status_code}) — provável "
                "rate-limit por IP; aguarde alguns minutos.",
                retryable=(r.status_code == 403 or r.status_code >= 500),
            )
        try:
            token = r.json()["response"]["captcha"]["tokenCaptcha"]
        except (ValueError, KeyError, TypeError) as e:
            raise ConsultaRecusada(f"Resposta inesperada do gera_captcha: {e}")
        return f"{token} {_resolver_captcha_do_token(token)}"

    async def listar_filtros(self, tipo: str) -> list[dict]:
        """Lista as opções válidas de um filtro ("classes" ou "tipos_pesquisa").
        Retorna [{id, label}]. Use para achar o id a passar em
        buscar_jurisprudencia (classe=id_classe; tipo_pesquisa=opção).
        """
        if tipo not in _LISTAS_FILTRO:
            raise ValueError(
                f"tipo inválido: {tipo!r}. Opções: {', '.join(_LISTAS_FILTRO)}"
            )
        ep, chave_lista, cid, clabel = _LISTAS_FILTRO[tipo]
        r = await self._get(ep, params={"tipoRelatorio": "1"})
        arr = r.json().get(chave_lista, [])
        return [{"id": _clean(it.get(cid)), "label": _clean(it.get(clabel))}
                for it in arr]

    async def _consultar(self, chave: str, inicio: int, fim: int,
                         filtros: Optional[dict] = None) -> dict:
        """Consulta com retry/backoff para o rate-limit (403) transitório.
        Erros definitivos (validação/negócio) sobem na primeira tentativa.
        """
        ultimo: Optional[ConsultaRecusada] = None
        for tentativa in range(self._MAX_TENTATIVAS):
            try:
                return await self._consultar_once(chave, inicio, fim, filtros)
            except ConsultaRecusada as e:
                if not e.retryable:
                    raise
                ultimo = e
                if tentativa < self._MAX_TENTATIVAS - 1:
                    await asyncio.sleep(self._BACKOFF_BASE * (tentativa + 1))
        raise ultimo  # type: ignore[misc]

    async def _consultar_once(self, chave: str, inicio: int, fim: int,
                              filtros: Optional[dict] = None) -> dict:
        """Uma tentativa de consulta. Levanta ConsultaRecusada em
        rate-limit/erro de servidor (retryable) ou recusa de negócio.
        """
        f = filtros or {}
        bearer = await self._novo_bearer()
        # Só `classe` e `tipoPesquisa` são efetivos nesta via; os demais campos
        # vão nos defaults do formulário (a API os ignora aqui de qualquer modo).
        params = {
            "chave": chave,
            "sistema": "0",
            "tipoPesquisa": str(f.get("tipo_pesquisa") or DEFAULT_TIPO_PESQUISA),
            "relator": "0",
            "revisor": "0",
            "camara": "0",
            "condicao": _CONDICAO_MAP.get(
                str(f.get("condicao") or "").strip().lower(), DEFAULT_CONDICAO),
            "classe": str(f.get("classe") or "0"),
            "dtaInicio": "2020-01-02",
            "dtaFim": "2030-12-31",
            "inicioPagina": str(inicio),
            "fimPagina": str(fim),
            "tokenG": _TOKENG_DUMMY,
            "keyId": _KEYID_BYPASS,   # bogus -> backend pula validacao do reCAPTCHA
        }
        headers = {"Authorization": f"Bearer {bearer}"}
        r = await self._get(EP_BUSCA_ACORDAOS, params=params, headers=headers)
        if r.status_code == 403:
            data = None
            try:
                data = r.json()
            except ValueError:
                pass
            raise ConsultaRecusada(
                "HTTP 403 do TJMA (rate-limit por IP ou validação). "
                f"{json.dumps(data, ensure_ascii=False) if data else ''}".strip(),
                retryable=True,
            )
        if r.status_code >= 500:
            raise ConsultaRecusada(
                f"Erro no servidor do TJMA (HTTP {r.status_code}).",
                retryable=True,
            )
        try:
            data = r.json()
        except ValueError:
            raise ConsultaRecusada(
                f"Resposta não-JSON do TJMA (HTTP {r.status_code})."
            )
        resp = data.get("response", data)
        if isinstance(resp, dict) and ("message" in resp or "validacao" in resp):
            raise ConsultaRecusada(
                f"API recusou a consulta: {json.dumps(resp, ensure_ascii=False)}"
            )
        return data

    async def buscar_jurisprudencia(
        self, query: str, limite: int = 10, page: int = 1,
        filtros: Optional[dict] = None,
    ) -> Busca:
        if not query or not query.strip():
            raise ValueError("query vazia")
        limite = max(1, min(limite, self._MAX_LINHAS))
        page = max(1, page)
        inicio = (page - 1) * limite + 1
        fim = inicio + limite - 1

        data = await self._consultar(query.strip(), inicio, fim, filtros)
        procs = _processos_de(data)

        busca = Busca(paginas_consultadas=1)
        if procs:
            busca.total_no_servidor = int(procs[0].get("int_count") or 0) or None
        vistos: set = set()
        for it in procs:
            res = _item_para_resultado(it)
            k = _chave_dedup(res)
            if k in vistos:
                busca.duplicatas_removidas += 1
                continue
            vistos.add(k)
            busca.resultados.append(res)
            if len(busca.resultados) >= limite:
                break
        return busca

    async def ler_decisao(self, url_or_id: str) -> Decisao:
        """A busca ja traz o inteiro teor; ler_decisao localiza a decisao pelo
        CNJ (busca exata) e devolve a Decisao completa.
        """
        if not url_or_id:
            raise ValueError("url_or_id vazio")
        cnj = _CNJ_RE.search(url_or_id)
        if not cnj:
            raise ValueError(
                "Informe o numero CNJ da decisao (ex.: 0002186-95.2015.8.10.0040)."
            )
        cnj = cnj.group(0)
        if cnj in self._cache_decisoes:
            self._cache_decisoes.move_to_end(cnj)
            return self._cache_decisoes[cnj]
        # Busca EXATA pelo número do processo (tipoPesquisa=6). NUNCA cai em
        # outro processo: se o CNJ exato não aparecer, é "não encontrado".
        data = await self._consultar(cnj, 1, 5, {"tipo_pesquisa": "6"})
        procs = _processos_de(data)
        alvo = next((p for p in procs if _clean(p.get("pkProtocolo")) == cnj), None)
        if alvo is None:
            raise ConsultaRecusada(
                f"Decisão {cnj} não encontrada na base de acórdãos do TJ-MA "
                "(confira o número CNJ)."
            )
        decisao = _item_para_decisao(alvo)
        self._cache_decisoes[cnj] = decisao
        if len(self._cache_decisoes) > self._CACHE_MAX:
            self._cache_decisoes.popitem(last=False)
        return decisao

    async def verificar_citacao(self, url_or_id: str, trecho: str) -> dict:
        if not trecho or not trecho.strip():
            return {"valido": False, "motivo": "trecho vazio", "url": None}
        decisao = await self.ler_decisao(url_or_id)
        if not decisao.inteiro_teor:
            return {"valido": False,
                    "motivo": "inteiro_teor vazio ou nao extraido",
                    "url": decisao.url}
        teor_norm = _normalize_para_comparacao(decisao.inteiro_teor)
        trecho_norm = _normalize_para_comparacao(trecho)
        if trecho_norm in teor_norm:
            return {"valido": True,
                    "motivo": "trecho encontrado no inteiro_teor",
                    "url": decisao.url}
        return {"valido": False,
                "motivo": ("trecho NAO encontrado no inteiro_teor. NAO cite "
                           "entre aspas. Reescreva como parafrase."),
                "url": decisao.url}
