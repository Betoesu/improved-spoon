# -*- coding: utf-8 -*-
"""
Carregamento e validação de configuração JSON com mensagens de erro amigáveis.
"""

import json
import os
import re
import sys
from tkinter import Tk, messagebox
from typing import Any, Dict, List

CONFIG_PATH = "stj_config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "advogados": [
        "RUDOLF SCHAITL",
        "JULIANA CAROLINA FRUTUOSO BIZARRIA",
        "SEVERINO DO RAMO CHAVES DE LIMA",
        "MARCIO CASTRO KAIK SIQUEIRA",
        "SERGIO HENRIQUE DE OLIVEIRA GOMES",
    ],
    "variacoes_bb": [
        "BANCO DO BRASIL SA.",
        "BANCO DOBRASIL S/A",
        "BANCO NOSSA CAIXA S/A",
        "BANCO DO BRASIL S.A",
        "B DO B S",
        "Banco do Estado do Piauí - BEP",
        "BANCODO BRASIL SA",
        "BANCO DOBRASIL S/A,",
        "BANCODO BRASIL S/A.",
        "BANCODO BRASIL",
        "Besc Distribuidora de Títulos e Valores Mobiliários S.A. (Bescval)",
        "BANCO DO BRASILS.A.",
        "BB LEASING S.A.ARRENDAMENTO MERCANTIL",
        "B B S",
        "BANCO DO BRASIL S A",
        "BB CORRETORA DE SEGUROS E ADMINISTRADORA DE BENS S/A",
        "Banco do Estado de Santa Catarina BESC",
        "BANCO DOBRASIL",
        "BB LEASING SA ARRENDAMENTO MERCANTIL",
        "BB-FINANCEIRA S.A.-CREDITO,FINANCIAMENTO E INVESTIMENTO",
        "BANCO DO BRASIL S/A.",
        "BANCO DO BRASILS/A",
        "BANCO DO BRASIL S A,",
        "PRESIDENTE DO BANCO DO BRASIL",
        "BANCO DO BRASIL SA",
        "BANCO CAIXA S/A",
        "BB CORRETORA DE SEGUROS E ADMINISTRADORA DE BENS S A",
        "BB CORRETORA DE SEGUROS E ADMINISTRADORA DE BENS SA",
        "BANCO DO BRASIL",
        "BANCO DO BRASIL S/A,",
        "BANCO DO BRASIL S/A",
        "BANCO DO BRASIL S.A.",
        "BANCO DO BRASIL S.A",
        "BANCODO BRASIL S/A",
        "BANCO DO BRASIL S.A,",
        "BB ADMINISTRADORA DE CONSÓRCIOS S",
        "BB.LEASING S.A.ARRENDAMENTO MERCANTIL",
        "BB ADMINISTRADORA DE CARTÕES DE CRÉDITO S/A",
    ],
    "blacklist_variacoes_bb": [
        "CAIXA DE PREVIDENCIA",
        "BANCO DO BRASIL - PREVI",
        "BANCO DO BRASIL S/A - PREVI",
        "BANCO DO BRASIL S A - PREVI",
        "BANCO DO BRASIL SA - PREVI",
        "BANCO DO BRASIL PREVI",
    ],
}

ERROR_TRANSLATIONS: Dict[str, str] = {
    "Expecting ':' delimiter": "Esperando o delimitador ':'",
    "Expecting ',' delimiter": "Esperando o delimitador ','",
    "Illegal trailing comma before end of object": "Vírgula extra antes do fim do dicionário",
    "Illegal trailing comma before end of array": "Vírgula extra antes do fim da lista",
    "Expecting value": "Esperando um valor",
    "Unterminated string starting at": "String não finalizada iniciada em",
    "Extra data": "Dados extras após o fim do documento",
    "Invalid control character at": "Possivel falta de aspas em uma string",
}


class PrecheckError(Exception):
    """Erro de pré-checagem de sintaxe JSON."""

    def __init__(self, lineno: int, colno: int, msg: str) -> None:
        self.lineno = lineno
        self.colno = colno
        self.msg = msg
        super().__init__(msg)


def _check_quotes_and_brackets(lines: List[str]) -> None:
    """Verifica paridade de aspas por linha e balanceamento de `{}` / `[]`."""
    stack = []
    for lineno, line in enumerate(lines, start=1):
        count = sum(
            1 for i, ch in enumerate(line)
            if ch == '"' and (i == 0 or line[i - 1] != "\\")
        )
        if count % 2 != 0:
            col = line.find('"') + 1
            raise PrecheckError(lineno, col, "Aspas duplas não abertas ou fechadas corretamente")

        for idx, ch in enumerate(line, start=1):
            if ch in "{[":
                stack.append((ch, lineno, idx))
            elif ch in "}]":
                if not stack:
                    raise PrecheckError(lineno, idx, f"Fechamento '{ch}' sem correspondente")
                opening, o_line, o_col = stack.pop()
                if not ((opening == "{" and ch == "}") or (opening == "[" and ch == "]")):
                    raise PrecheckError(
                        lineno,
                        idx,
                        f"Fechamento '{ch}' não corresponde a '{opening}' aberto em linha {o_line}, coluna {o_col}",
                    )

    if stack:
        opening, o_line, o_col = stack[-1]
        expected = "}" if opening == "{" else "]"
        raise PrecheckError(o_line, o_col, f"Falta '{expected}' para fechar '{opening}' aqui")


def _check_missing_commas(lines: List[str]) -> None:
    """Verifica ausência de vírgulas ao final de pares chave:valor ou itens de lista."""
    kv_pattern = re.compile(r'^\s*".+"\s*:\s*".+"\s*$')
    key_start = re.compile(r'^\s*".+"\s*:')
    for i in range(len(lines) - 1):
        curr = lines[i].rstrip("\n")
        nextl = lines[i + 1].lstrip()
        if kv_pattern.match(curr) and key_start.match(nextl) and not curr.strip().endswith(","):
            raise PrecheckError(i + 1, len(curr), "Falta vírgula ao final desta entrada")

    elem_pattern = re.compile(r'^\s*".+"\s*$')
    for i in range(len(lines) - 1):
        curr = lines[i].rstrip("\n")
        if not curr.strip().endswith(",") and elem_pattern.match(curr):
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].lstrip().startswith('"'):
                raise PrecheckError(i + 1, len(curr), "Falta vírgula ao final deste elemento de lista")


def precheck_syntax(path: str) -> None:
    """Detecta erros comuns de sintaxe em um arquivo JSON antes do parse oficial."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    _check_quotes_and_brackets(lines)
    _check_missing_commas(lines)


def show_error(title: str, mensagem: str) -> None:
    """Exibe MessageBox de erro e encerra o processo."""
    root = Tk()
    root.withdraw()
    messagebox.showerror(title, mensagem)
    root.destroy()
    sys.exit(1)


def show_json_error(e: json.JSONDecodeError, path: str) -> None:
    """Formata e apresenta erros de parsing JSON com tradução para PT-BR."""
    pt_msg = ERROR_TRANSLATIONS.get(e.msg, e.msg)
    try:
        with open(path, "r", encoding="utf-8") as f:
            error_line = f.readlines()[e.lineno - 1].rstrip("\n")
    except Exception:
        error_line = "<não foi possível obter a linha>"

    mensagem = (
        f"Erro ao carregar '{os.path.basename(path)}':\n\n"
        f"Linha {e.lineno}, Coluna {e.colno}\n"
        f"{pt_msg}\n\n"
        f"Conteúdo da linha:\n{error_line}"
    )
    show_error("Erro de Configuração JSON", mensagem)


def show_precheck_error(e: PrecheckError, path: str) -> None:
    """Formata e apresenta erros detectados na pré-checagem de sintaxe."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            line = f.readlines()[e.lineno - 1].rstrip("\n")
    except Exception:
        line = "<não foi possível obter a linha>"

    mensagem = (
        f"Erro de sintaxe em '{os.path.basename(path)}':\n\n"
        f"Linha {e.lineno}, Coluna {e.colno}\n"
        f"{e.msg}\n\n"
        f"Conteúdo da linha:\n{line}"
    )
    show_error("Erro no JSON (pré-checagem)", mensagem)


def load_config() -> Dict[str, Any]:
    """Carrega o stj_config.json com fallback para DEFAULT_CONFIG e validações."""
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=4)

    try:
        precheck_syntax(CONFIG_PATH)
    except PrecheckError as e:
        show_precheck_error(e, CONFIG_PATH)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        show_json_error(e, CONFIG_PATH)
    except Exception as e:
        show_error("Erro ao abrir config.json", str(e))


def obter_lista_advogados(config: Dict[str, Any]) -> List[str]:
    """
    Extrai a lista ordenada de advogados a partir da configuração carregada.

    Aceita tanto o formato padrão em lista (`["ADV 1", "ADV 2", ...]`) quanto o
    formato legado em dicionário por dígito (`{"0": "ADV 1", ...}`), extraindo
    os nomes na ordem em que aparecem sem duplicatas adjacentes/globais.
    """
    raw_advogados = config.get("advogados", [])
    if isinstance(raw_advogados, list):
        return [str(nome).strip() for nome in raw_advogados if str(nome).strip()]
    if isinstance(raw_advogados, dict):
        nomes_ordenados: List[str] = []
        for nome in raw_advogados.values():
            nome_limpo = str(nome).strip()
            if nome_limpo and nome_limpo not in nomes_ordenados:
                nomes_ordenados.append(nome_limpo)
        return nomes_ordenados
    return []
