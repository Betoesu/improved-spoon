# -*- coding: utf-8 -*-
"""
Publicações do STJ: listagem, indicação/descarta e métricas recentes.
"""

from datetime import datetime, timedelta
import time
from typing import Any, Dict, List
import unicodedata

import pandas as pd

from DijurLib.api.publicacoes import (
    consultar_tratamento_publicacoes,
    descartar_publicacao,
    detalhar_publicacao,
    indicar_publicacao,
    listar_publicacoes,
)

from libs.erros import ErroEsperado
from libs.helper import montar_npj, update_log

TOKEN_ATA_DISTRIBUICAO = "tipodedocumento:atadedistribuicao"


def _normalizar_texto_publicacao(texto: str) -> str:
    """Normaliza o texto da publicação (ASCII, minúsculas e sem espaços)."""
    sem_acentos = (
        unicodedata.normalize("NFKD", texto)
        .encode("ASCII", "ignore")
        .decode("ASCII")
    )
    return "".join(sem_acentos.lower().split())


def pegar_publicacoes(driver: Any, df: pd.DataFrame) -> pd.DataFrame:
    """Busca publicações pendentes do STJ e preenche o DataFrame da automação."""
    df = df.iloc[0:0]

    objetos_publicacoes = listar_publicacoes(driver, "pendente", "stj")

    if objetos_publicacoes is None or objetos_publicacoes["quantidadeRegistro"] == 0:
        return []

    lista_publicacoes = objetos_publicacoes["listaPublicacao"]

    publicacoes = []
    id_publicacao = []

    for publicacao in lista_publicacoes:
        publicacoes.append(publicacao["codigoExternoProcessoInteresse"])
        id_publicacao.append(publicacao["numeroPublicacaoJudicial"])

    data_atual = datetime.now().strftime("%d/%m/%Y")
    datas = [data_atual] * len(publicacoes)

    df["CNJ Publicação"] = publicacoes
    df["ID Publicação"] = id_publicacao
    df["Data"] = datas

    return df


def indica_pubicacao(
    driver: Any,
    id_publicacao: str,
    id_npj: str,
    log_widget: Any,
) -> str:
    """Indica (complementa) a publicação no STJ ou descarta conforme regras."""
    detalhe = detalhar_publicacao(driver, id_publicacao)
    texto_publicacao = _normalizar_texto_publicacao(
        detalhe["data"]["textoPublicacaoJudicial"]
    )

    if TOKEN_ATA_DISTRIBUICAO in texto_publicacao:
        npj = montar_npj(driver, id_npj)

        update_log(log_widget, f"Descartando publicação com id {id_publicacao}")
        response = descartar_publicacao(driver, id_publicacao, npj)

        if response["status"] != "OK":
            raise ErroEsperado(f"Erro ao descartar publicação {id_publicacao}")

        return "Descartada"

    update_log(log_widget, f"Indicando publicação com id {id_publicacao}")
    response = indicar_publicacao(driver, id_publicacao, id_npj)

    if response["status"] != "OK":
        raise ErroEsperado(f"Erro ao Complementando publicação {id_publicacao}")

    return "Complementada"


def pegar_dados_tratadas_ultimos_7_dias(driver: Any) -> List[Dict[str, int]]:
    """Coleta estatísticas diárias de tratamento de publicações em janela recente."""
    dados_bbts_lista = []

    for i in range(8, 0, -1):
        date = datetime.now() - timedelta(days=i)
        date_str = date.strftime("%d/%m/%Y")

        resultado = consultar_tratamento_publicacoes(driver, date_str, date_str)

        total_publicacoes = sum(resultado.values())
        tratadas = resultado.get("Tratada", 0)
        pendentes = resultado.get("Pendente", 0)
        em_tratamento = resultado.get("Em tratamento", 0)
        complementadas = resultado.get("Complementada", 0)
        descartadas = resultado.get("Descartada", 0)
        descartadas_banco_nao_parte = resultado.get("Descartada banco não parte", 0)

        dados_bbts = {
            "data": date_str,
            "total_publicacoes": total_publicacoes,
            "tratadas": tratadas,
            "pendentes": pendentes,
            "em_tratamento": em_tratamento,
            "complementadas": complementadas,
            "descartadas": descartadas,
            "descartadas_banco_nao_parte": descartadas_banco_nao_parte,
        }

        dados_bbts_lista.append(dados_bbts)
        time.sleep(0.4)

    return dados_bbts_lista
