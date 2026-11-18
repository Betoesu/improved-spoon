# -*- coding: utf-8 -*-
"""
Geração e distribuição de relatórios de publicações do STJ.
"""

from datetime import datetime
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ONEDRIVE_TARGET_FOLDER = "Relatórios Publicações DIGOR - Documentos"

COLUNAS_CSV_POWERBI: List[str] = [
    "CNJ Publicação",
    "ID Publicação",
    "Status",
    "Data",
    "Hora",
    "Matrícula",
    "Tipo Processo",
    "BB Parte",
    "Polo",
    "Tramitacao",
]


def get_onedrive_path() -> Optional[str]:
    """Localiza o caminho da pasta-alvo da equipe no OneDrive."""
    onedrive_root = os.getenv("OneDrive")

    if onedrive_root:
        for dirpath, dirnames, _ in os.walk(onedrive_root):
            for dirname in dirnames:
                if dirname == ONEDRIVE_TARGET_FOLDER:
                    return os.path.join(dirpath, dirname)
    return None


def get_unique_filename(base_name: str, extension: str) -> str:
    """Gera um nome de arquivo único evitando sobrescrita."""
    candidate = f"{base_name}{extension}"
    if not os.path.exists(candidate):
        return candidate

    counter = 2
    while os.path.exists(f"{base_name}_{counter}{extension}"):
        counter += 1
    return f"{base_name}_{counter}{extension}"


def get_unique_filename_in_dir(directory: str, filename: str) -> str:
    """Gera um nome de arquivo único em um diretório evitando sobrescrita."""
    base, extension = os.path.splitext(filename)
    full_path = os.path.join(directory, filename)
    if not os.path.exists(full_path):
        return full_path
    counter = 2
    while os.path.exists(os.path.join(directory, f"{base}_{counter}{extension}")):
        counter += 1
    return os.path.join(directory, f"{base}_{counter}{extension}")


def _salvar_relatorios_locais(df: pd.DataFrame) -> Tuple[str, str, pd.DataFrame]:
    """Salva o relatório geral e o relatório manual na pasta local `Relatorios/`."""
    if "Advogado Responsável" not in df.columns:
        df["Advogado Responsável"] = "Nenhum"
    else:
        df["Advogado Responsável"] = (
            df["Advogado Responsável"]
            .fillna("Nenhum")
            .astype(str)
            .replace({"": "Nenhum", "nan": "Nenhum", "None": "Nenhum"})
        )

    base_name = f"Relatorios/STJ_relatorio_{datetime.today().strftime('%d-%m-%Y')}"

    general_report_filename = get_unique_filename(base_name, ".xlsx")
    df.to_excel(general_report_filename, index=False)

    df_manual = df[df["Status"].str.startswith("Manual")]
    df_manual = df_manual.sort_values(by=["Status", "CNJ Publicação"])

    manual_report_filename = get_unique_filename(base_name + "_manual", ".xlsx")
    df_manual.to_excel(manual_report_filename, index=False)

    return general_report_filename, manual_report_filename, df_manual


def salvar_relatorios_simulacao_advogados(distribuidor_advogados: Any) -> Tuple[str, str]:
    """
    Gera e salva os relatórios de simulação da `versao_lista_advogados` na pasta `Relatorios/`:
        1) TXT (`STJ_distribuicao_advogados_<data>.txt`) com a contagem e detalhamento por advogado.
        2) XLSX (`STJ_distribuicao_advogados_<data>.xlsx`) passo a passo por coluna e contagem
           por advogado nas últimas colunas.
    """
    os.makedirs("Relatorios", exist_ok=True)
    base_name = f"Relatorios/STJ_distribuicao_advogados_{datetime.today().strftime('%d-%m-%Y')}"

    txt_report_filename = get_unique_filename(base_name, ".txt")
    conteudo_txt = distribuidor_advogados.formatar_relatorio_txt()
    with open(txt_report_filename, "w", encoding="utf-8") as f:
        f.write(conteudo_txt)

    xlsx_report_filename = get_unique_filename(base_name, ".xlsx")
    df_xlsx = distribuidor_advogados.gerar_dataframe_xlsx()
    df_xlsx.to_excel(xlsx_report_filename, index=False)

    return txt_report_filename, xlsx_report_filename


def _publicar_no_onedrive(
    onedrive_path: str,
    df: pd.DataFrame,
    df_manual: pd.DataFrame,
    general_report_filename: str,
    manual_report_filename: str,
    dados_bbts: List[Dict[str, Any]],
) -> None:
    """Salva os relatórios Excel no OneDrive e atualiza os CSVs do Power BI."""
    onedrive_stj_dir = os.path.join(onedrive_path, "Relatórios", "STJ")

    general_report_onedrive = get_unique_filename_in_dir(
        onedrive_stj_dir, os.path.basename(general_report_filename.split("/")[1])
    )
    manual_report_onedrive = get_unique_filename_in_dir(
        onedrive_stj_dir, os.path.basename(manual_report_filename.split("/")[1])
    )
    df.to_excel(general_report_onedrive, index=False)
    df_manual.to_excel(manual_report_onedrive, index=False)

    df_to_csv = df[COLUNAS_CSV_POWERBI].assign(Tribunal="STJ")
    df_to_csv.to_csv(f"{onedrive_path}/Publicacoes.csv", mode="a", header=False, index=False)

    bbts_csv = pd.read_csv(f"{onedrive_path}/resumo_publicacoes.csv", dtype={"data": str})
    dados_bbts_df = pd.DataFrame(dados_bbts)
    bbts_csv = pd.concat(
        [bbts_csv, dados_bbts_df[~dados_bbts_df["data"].isin(bbts_csv["data"])]],
        ignore_index=True,
    )
    bbts_csv.to_csv(f"{onedrive_path}/resumo_publicacoes.csv", index=False)


def _limpar_cache_execucao(publicacoes_txt: str, excel_automacao: str) -> None:
    """Zera o arquivo TXT de IDs processados e mantém apenas o cabeçalho no Excel de cache."""
    with open(publicacoes_txt, "w"):
        pass

    df_cache = pd.read_excel(excel_automacao)
    headers = df_cache.head(0)
    headers.to_excel(excel_automacao, index=False)


def gerar_relatorio(
    df: pd.DataFrame,
    excel_automacao: str,
    publicacoes_txt: str,
    dados_bbts: List[Dict[str, Any]],
    distribuidor_advogados: Optional[Any] = None,
) -> None:
    """Gera arquivos de relatório, publica no OneDrive e atualiza CSVs de apoio."""
    if distribuidor_advogados is not None:
        try:
            salvar_relatorios_simulacao_advogados(distribuidor_advogados)
        except Exception:
            pass

    general_report_filename, manual_report_filename, df_manual = _salvar_relatorios_locais(df)

    onedrive_path = get_onedrive_path()
    if not onedrive_path:
        raise Exception(
            "Pasta do OneDrive não encontrada, certifique de criar o atalho da pasta "
            "'Relatórios Publicações DIGOR - Documentos' no OneDrive"
        )

    _publicar_no_onedrive(
        onedrive_path,
        df,
        df_manual,
        general_report_filename,
        manual_report_filename,
        dados_bbts,
    )

    _limpar_cache_execucao(publicacoes_txt, excel_automacao)
