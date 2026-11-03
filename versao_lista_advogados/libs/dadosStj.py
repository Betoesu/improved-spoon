# -*- coding: utf-8 -*-
"""
Coleta e tratamento de dados de publicações do STJ (PJe + site público).
"""

from datetime import datetime, timedelta
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

from DijurLib.api.consultaProcessos import get_processo_pje

from libs.config import load_config
from libs.helper import update_log

CONFIG = load_config()
VARIACOES_BB = CONFIG.get("variacoes_bb", [])
BLACK_LIST_VARIACOES_BB = CONFIG.get("blacklist_variacoes_bb", [])

LISTA_SEGREDO_DE_JUSTICA = ["B DO B S", "B B S"]
LISTA_ATIVOS = ["AGRAVANTE", "RECORRENTE", "SUSCITANTE", "EMBARGANTE", "EXECUTANTE", "AUTOR", "IMPETRANTE", "RECLAMANTE"]
LISTA_PASSIVOS = ["AGRAVADO", "RECORRIDO", "SUSCITADO", "EMBARGADO", "REQUERIDO", "REU", "RÉU", "EXECUTADO", "IMPETRADO", "RECLAMADO"]
LISTA_NEUTROS = ["INTERES"]
LISTA_TRAMITACAO = [
    "Presidencia", "Subsecao I Especializ Dissidios Individuais", "Subsecao Ii Especiali Dissidios Individuais",
    "01 Turma", "02 Turma", "03 Turma", "04 Turma", "05 Turma", "06 Turma", "07 Turma", "08 Turma",
    "01 Secao", "02 Secao", "03 Secao", "04 Secao", "05 Secao", "06 Secao", "07 Secao", "08 Secao",
    "Órgão Especial",
]

MAPA_TRAMITACOES = {
    "PRESIDENTE": "Presidencia",
    "PRIMEIRA TURMA": "01 TURMA",
    "SEGUNDA TURMA": "02 TURMA",
    "TERCEIRA TURMA": "03 TURMA",
    "QUARTA TURMA": "04 TURMA",
    "QUINTA TURMA": "05 TURMA",
    "SEXTA TURMA": "06 TURMA",
    "SÉTIMA TURMA": "07 TURMA",
    "OITAVA TURMA": "08 TURMA",
    "NONA TURMA": "09 TURMA",
    "PRIMEIRA SEÇÃO": "01 SEÇÃO",
    "SEGUNDA SEÇÃO": "02 SEÇÃO",
    "TERCEIRA SEÇÃO": "03 SEÇÃO",
    "QUARTA SEÇÃO": "04 SEÇÃO",
    "QUINTA SEÇÃO": "05 SEÇÃO",
    "SEXTA SEÇÃO": "06 SEÇÃO",
    "SÉTIMA SEÇÃO": "07 SEÇÃO",
    "OITAVA SEÇÃO": "08 SEÇÃO",
    "NONA SEÇÃO": "09 SEÇÃO",
}

POLO_MAPPING = {"A": "Ativo", "P": "Passivo", "N": "Neutro"}

COLUNAS_DADOS_PJE = [
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


def is_abbreviated_name(name: str):
    """Verifica se um nome está abreviado (segredo de justiça)."""
    name = name.strip()
    padrao = re.compile(r"\bB(\.B| DO B| DO B S| DO B SA|\.DO\.B\.)?(\.S| S| SA)?\b")
    return padrao.match(name)


def parse_tramitacao(tramitacao_text: str) -> str:
    """Converte o texto de tramitação para um formato padronizado."""
    for key, value in MAPA_TRAMITACOES.items():
        if key in tramitacao_text:
            return value
    return tramitacao_text


def pegar_tramitacao(driver: Any, wait: Any, publicacao: str) -> str:
    """Consulta tramitação diretamente na página pública do STJ (fallback legado)."""
    driver.get("https://processo.stj.jus.br/processo/pesquisa/")
    campo_pesquisa = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="idNumeroRegistro"]')))
    campo_pesquisa.clear()
    campo_pesquisa.send_keys(publicacao)
    campo_pesquisa.send_keys(Keys.ENTER)
    tramitacao_text = wait.until(
        EC.presence_of_element_located((By.XPATH, '//*[@id="idProcessoDetalhesBloco2"]/div[1]/span[2]'))
    ).text
    return parse_tramitacao(tramitacao_text)


def extrair_nome(destinatario: Dict[str, Any]) -> str:
    """Extrai o nome limpo do destinatário a partir da estrutura retornada pelo PJe."""
    nome_completo = destinatario["nome"]
    nome = nome_completo.split(".", 1)[1].split("(")[0].strip()
    return nome


def remove_html_tags(text: str) -> str:
    """Remove tags HTML de uma string usando expressão regular."""
    return re.sub(r"<.*?>", "", text)


def _consultar_pje_com_retry(
    semana_anterior_str: str,
    data_atual_str: str,
    processo: str,
    log_widget: Any,
    max_tentativas: int = 3,
) -> Optional[Dict[str, Any]]:
    """Consulta a API do PJe para o processo informado com até 3 tentativas."""
    tentativa = 0
    while True:
        try:
            return get_processo_pje(
                dataDisponibilizacaoInicio=semana_anterior_str,
                dataDisponibilizacaoFim=data_atual_str,
                texto=processo,
                siglaTribunal="STJ",
            )
        except Exception as e:
            update_log(log_widget, f"Erro ao consultar API CNJ, tentando novamente, {tentativa + 1}/{max_tentativas}")
            time.sleep(2)
            tentativa += 1
            if tentativa >= max_tentativas:
                update_log(log_widget, f"Não foi possivel consultar dados via API: {e}")
                return None


def _marcar_linha_indefinida(df: Any, index: Any, status_msg: str) -> None:
    """Preenche a linha do DataFrame com 'Indefinido' e registra o motivo em Status."""
    df.at[index, "Status"] = status_msg
    for coluna in COLUNAS_DADOS_PJE:
        df.at[index, coluna] = "Indefinido"


def _extrair_listas_partes(dados_item: Dict[str, Any]) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Separa os destinatários do PJe nas listas de polos Ativo, Passivo e Neutro."""
    for destinatario in dados_item.get("destinatarios", []):
        if "(INTERESSADO)" in destinatario.get("nome", ""):
            destinatario["polo"] = "N"

    ativos = [extrair_nome(dest) for dest in dados_item.get("destinatarios", []) if dest.get("polo") == "A"]
    passivos = [extrair_nome(dest) for dest in dados_item.get("destinatarios", []) if dest.get("polo") == "P"]
    neutros = [extrair_nome(dest) for dest in dados_item.get("destinatarios", []) if dest.get("polo") == "N"]
    destinatarios = ativos + passivos + neutros

    if neutros == []:
        neutros.append("Não informado pelo PJe")

    return ativos, passivos, neutros, destinatarios


def _identificar_polo_bb(
    destinatarios: List[str],
    ativos: List[str],
    passivos: List[str],
    neutros: List[str],
    clean_text: str,
) -> Tuple[bool, str]:
    """Verifica se o Banco do Brasil figura como parte/interessado e retorna (bb_parte, polo_banco)."""
    polo_banco = "Indefinido"
    bb_parte = False

    for dest in destinatarios:
        dest_upper = dest.upper()
        if any(dest_upper in variacao.upper() for variacao in VARIACOES_BB):
            if any(blacklist.upper() in dest_upper for blacklist in BLACK_LIST_VARIACOES_BB):
                continue

            if dest in ativos:
                polo_banco = POLO_MAPPING.get("A")
            elif dest in passivos:
                polo_banco = POLO_MAPPING.get("P")
            elif dest in neutros:
                polo_banco = POLO_MAPPING.get("N")
            bb_parte = True
            break

    if not bb_parte and any(f"INTERESSADO:{variacao.replace(' ', '')}" in clean_text for variacao in VARIACOES_BB):
        polo_banco = "Neutro"
        bb_parte = True

        if "Não informado pelo PJe" in neutros:
            neutros.remove("Não informado pelo PJe")

        neutros.append("BANCO DO BRASIL S A")

    return bb_parte, polo_banco


def _identificar_tramitacao(dados_item: Dict[str, Any], texto_publicacao: str) -> str:
    """Determina o órgão de tramitação (Presidência, Turma ou Seção) a partir dos metadados do PJe."""
    tipo_documento = dados_item.get("tipoDocumento", "")

    if "ATA DE DISTRIBUIÇÃO" in tipo_documento.upper():
        return "Presidencia"

    orgao = dados_item.get("nomeOrgao", "Indefinido").upper()
    tramitacao = "Indefinido"

    if "SEÇÃO" in orgao or "TURMA" in orgao:
        for chave, valor in MAPA_TRAMITACOES.items():
            if chave in orgao:
                tramitacao = valor
                break
            else:
                tramitacao = "Indefinido"

    if tramitacao == "Indefinido":
        if "MINISTRO PRESIDENTE DO STJ" in texto_publicacao or "MINISTRO VICE-PRESIDENTE DO STJ" in texto_publicacao:
            tramitacao = "Presidencia"

    return tramitacao


def pegar_dados_cnj(driver: Any, wait: Any, df: Any, log_widget: Any) -> Any:
    """Extrai os dados dos processos do PJe e atualiza o DataFrame."""
    data_atual = datetime.now()
    semana_anterior = data_atual - timedelta(days=7)
    data_atual_str = data_atual.strftime("%Y-%m-%d")
    semana_anterior_str = semana_anterior.strftime("%Y-%m-%d")

    for index, row in df.iterrows():
        processo_sem_mascara = row["CNJ Publicação"]
        processo = f"({processo_sem_mascara[:4]}/{processo_sem_mascara[4:11]}-{processo_sem_mascara[11:]})"

        update_log(log_widget, f"Processo {str(index + 1)}/{len(df)} - {processo}")
        time.sleep(0.8)

        dados = _consultar_pje_com_retry(semana_anterior_str, data_atual_str, processo, log_widget)

        if not dados or not dados["items"]:
            _marcar_linha_indefinida(df, index, "Manual: Erro ao consultar processo")
            continue
        elif dados.get("count", 0) == 0:
            _marcar_linha_indefinida(df, index, "Manual: Processo não encontrado")
            continue

        dados_item = dados["items"][0]
        ativos, passivos, neutros, destinatarios = _extrair_listas_partes(dados_item)

        segredo_justica = False
        for nome in ativos + passivos + neutros:
            if is_abbreviated_name(nome):
                df.at[index, "Status"] = "Manual: Processo em segredo de justiça"
                segredo_justica = True
                break

        tipo_processo = dados_item.get("nomeClasse", "Indefinido")
        texto_publicacao = dados_item.get("texto", "")
        clean_text = remove_html_tags(texto_publicacao).replace("\n", " ").replace(" ", "").upper()

        padrao_data = re.compile(r"\d{2}/\d{2}/\d{4}")
        data_autuacao = padrao_data.search(texto_publicacao)
        autuacao = data_autuacao.group(0) if data_autuacao else dados_item.get("datadisponibilizacao", "Indefinido")

        padrao_outros = re.search(r"\s(\d+)(?=/[A-Za-z]{2})", texto_publicacao)
        outros = padrao_outros.group(1) if padrao_outros else "Indefinido"

        numero_unico = dados_item.get("numeroprocessocommascara", "")

        bb_parte, polo_banco = _identificar_polo_bb(destinatarios, ativos, passivos, neutros, clean_text)
        tramitacao = _identificar_tramitacao(dados_item, texto_publicacao)

        parte = "BB Parte" if bb_parte else ("Indefinido" if segredo_justica else "BB Não parte")

        df.at[index, "Outros"] = outros
        df.at[index, "Tipo Processo"] = tipo_processo.upper()
        df.at[index, "Autuação"] = autuacao
        df.at[index, "Numero Único"] = numero_unico
        df.at[index, "BB Parte"] = parte
        df.at[index, "Polo"] = polo_banco
        df.at[index, "Tramitacao"] = tramitacao
        df.at[index, "Ativos"] = str(ativos)
        df.at[index, "Passivos"] = str(passivos)
        df.at[index, "Neutros"] = str(neutros)

        if "Indefinido" in [tipo_processo, autuacao, tramitacao] and parte != "BB Não parte":
            df.at[index, "Status"] = "Manual: Dados indefinidos"

    return df
