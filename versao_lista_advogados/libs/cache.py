# -*- coding: utf-8 -*-
"""
Utilitários de cache para a automação do STJ.

Este módulo provê a função `cria_cache`, responsável por garantir a existência
da estrutura mínima de diretórios e arquivos de cache necessária ao
funcionamento da automação. Caso não existam, são criados:

- Diretório "cache" (arquivos de trabalho temporários)
- Diretório "Relatorios" (saídas consolidadas)
- Arquivo "cache/publicacoes.txt" (controle de publicações já processadas)
- Planilha "cache/automacao_STJ.xlsx" com colunas padrão para a automação

Observações:
- Os caminhos retornados são relativos ao diretório de execução do script.
- Em caso de falha de criação de diretórios/arquivos, exceções do tipo
  OSError/IOError podem ser propagadas para tratamento pelo chamador.
"""

import os
from typing import List, Tuple

import pandas as pd

# ------------------------------------------------------------------------------
# Constantes de diretórios, arquivos e estrutura da planilha de cache
# ------------------------------------------------------------------------------
CACHE_DIR = "cache"
REPORTS_DIR = "Relatorios"
CACHE_PUBLICACOES_FILENAME = "publicacoes.txt"
EXCEL_AUTOMACAO_FILENAME = "automacao_STJ.xlsx"

COLUNAS_AUTOMACAO: List[str] = [
    "CNJ Publicação",
    "ID Publicação",
    "Status",
    "Data",
    "Hora",
    "NPJ",
    "Matrícula",
    "Outros",
    "Tipo Processo",
    "Autuação",
    "Numero Único",
    "BB Parte",
    "Polo",
    "Tramitacao",
    "Ativos",
    "Passivos",
    "Neutros",
]


def cria_cache() -> Tuple[str, str]:
    """Garante a estrutura de cache e retorna os caminhos relevantes."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    cache_publicacoes_path = os.path.join(CACHE_DIR, CACHE_PUBLICACOES_FILENAME)
    excel_automacao_path = os.path.join(CACHE_DIR, EXCEL_AUTOMACAO_FILENAME)

    if not os.path.exists(cache_publicacoes_path):
        with open(cache_publicacoes_path, "w", encoding="utf-8"):
            pass

    if not os.path.exists(excel_automacao_path):
        df = pd.DataFrame(columns=COLUNAS_AUTOMACAO)
        df.to_excel(excel_automacao_path, index=False)

    return cache_publicacoes_path, excel_automacao_path
