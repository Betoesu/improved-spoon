# -*- coding: utf-8 -*-
"""
Helpers de portal e NPJ: URL base, conferência de partes, montagem de NPJ,
atualização de log em Tkinter e extração de fatias do CNJ.
"""

import tkinter as tk
from typing import Any, Dict, Sequence

from DijurLib.api.base import comparar_str
from DijurLib.api.consulta import get_processo_id_npj

PORTAL_BASE_URL = "https://juridico.intranet.bb.com.br/paj/app/"
DEFAULT_SIMILARITY_THRESHOLD = 0.85


def ensure_base_url(driver: Any) -> None:
    """Garante que o driver esteja na URL base do portal."""
    if driver.current_url != PORTAL_BASE_URL:
        driver.get(PORTAL_BASE_URL)


def check_partes(
    partes_cadastradas: Sequence[Dict[str, Any]],
    nomes_list: Sequence[str],
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> bool:
    """Verifica se alguma parte cadastrada corresponde a um dos nomes fornecidos."""
    for parte in partes_cadastradas:
        for nome in nomes_list:
            score = comparar_str(parte["nomeRazaoSocialClientePessoa"], nome)
            if score >= threshold:
                return True
    return False


def montar_npj(driver: Any, id_npj: str) -> str:
    """Monta a máscara do NPJ a partir do id do NPJ."""
    dados_npj: Dict[str, Any] = get_processo_id_npj(driver, id_npj)

    inicio_npj = str(dados_npj["numeroProcessoPrincipal"])
    variacao_npj = str(dados_npj["numeroOrdemVariacao"]).zfill(3)

    inicio_npj_formatado = f"{inicio_npj[:4]}/{inicio_npj[4:]}"
    npj = f"{inicio_npj_formatado}-{variacao_npj}"
    return npj


def update_log(widget: tk.Widget, message: str) -> None:
    """Escreve uma nova linha no widget de log e força atualização visual."""
    widget.config(state="normal")
    widget.insert(tk.END, message + "\n")
    widget.see(tk.END)
    widget.config(state="disabled")
    widget.update_idletasks()
