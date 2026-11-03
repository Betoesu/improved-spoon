# -*- coding: utf-8 -*-
"""
Regras para identificar e descartar publicações como "BB Não Parte".
"""

from typing import Any, Dict, List, Tuple

from DijurLib.api.consulta import get_processo_numerodoprocesso_simples
from DijurLib.api.publicacoes import (
    descartar_publicacao_bbnaoparte,
    listar_publicacoes_historico,
)

from libs.erros import ErroEsperado

ESTADO_DESCARTADA_BB_NAO_PARTE = "descartadabbnaoparte"
ESTADO_PENDENTE = "pendente"
MINIMO_HISTORICO_DESCARTE = 3


def conferir_bbnaoparte(
    driver: Any,
    numero_publicacao: str,
    id_publicacao: str,
    cnj_publicacao: str,
) -> Tuple[bool, int]:
    """Verifica se a publicação deve ser descartada como 'BB Não Parte'."""
    pesquisa: Dict[str, Any] = get_processo_numerodoprocesso_simples(driver, numero_publicacao)
    lista_npj = pesquisa.get("processos")

    if lista_npj is None:
        return analisar_publicacao_bbnaoparte(driver, cnj_publicacao, id_publicacao)
    else:
        return False, 0


def analisar_publicacao_bbnaoparte(
    driver: Any,
    cnj_publicacao: str,
    id_publicacao: str,
) -> Tuple[bool, int]:
    """Analisa o histórico e, quando aplicável, descarta como 'BB Não Parte'."""
    resultado: Dict[str, Any] = listar_publicacoes_historico(driver, "todas", cnj_publicacao)
    lista_publicacoes: List[Dict[str, Any]] = resultado.get("listaPublicacao", [])

    publicacoes_tratadas = [
        pub for pub in lista_publicacoes
        if pub.get("codigoEstadoPublicacaoJudicial") not in (ESTADO_DESCARTADA_BB_NAO_PARTE, ESTADO_PENDENTE)
    ]

    if publicacoes_tratadas:
        return False, 0

    publicacoes_bbnaoparte = [
        pub for pub in lista_publicacoes
        if pub.get("codigoEstadoPublicacaoJudicial") == ESTADO_DESCARTADA_BB_NAO_PARTE
    ]

    if len(publicacoes_bbnaoparte) < MINIMO_HISTORICO_DESCARTE:
        return False, 0

    descarte = descartar_publicacao_bbnaoparte(driver, id_publicacao)
    if descarte.get("status") == "OK":
        return True, len(publicacoes_bbnaoparte)

    raise ErroEsperado("Erro ao descartar publicação bb não parte")
