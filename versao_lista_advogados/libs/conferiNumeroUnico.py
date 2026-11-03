# -*- coding: utf-8 -*-
"""
Consulta e validação do Número Único CNJ (NPJ principal).
"""

from typing import Any, Dict, List, Optional

from DijurLib.api.consulta import get_processo_numerodoprocesso_simples

from libs.erros import ErroEsperado


def conferir_numero_unico(driver: Any, numero_unico: str) -> str:
    """Retorna o NPJ principal a partir do Número Único CNJ informado."""
    if not numero_unico:
        raise ValueError("O parâmetro 'numero_unico' não pode ser vazio")

    pesquisa: Dict[str, Any] = get_processo_numerodoprocesso_simples(driver, numero_unico)
    lista_npj: Optional[List[Dict[str, Any]]] = pesquisa.get("processos")

    if not lista_npj:
        raise ErroEsperado(f"Processo {numero_unico} não encontrado")

    processos_validos: List[Dict[str, Any]] = [
        p for p in lista_npj
        if p.get("situacao", "") != "Excluído"
    ]
    if not processos_validos:
        raise ErroEsperado("Nenhum npj encontrado")

    if len(processos_validos) == 1:
        numero_principal = processos_validos[0].get("numeroProcessoPrincipal")
        if not numero_principal:
            raise KeyError("Chave 'numeroProcessoPrincipal' ausente no resultado")
        return str(numero_principal)

    principais: List[str] = []
    for processo in processos_validos:
        numero_principal = processo.get("numeroProcessoPrincipal")
        if not numero_principal:
            raise KeyError("Chave 'numeroProcessoPrincipal' ausente em um dos resultados")
        principais.append(str(numero_principal))

    principais_unicos = set(principais)

    if len(principais_unicos) == 1:
        return next(iter(principais_unicos))

    raise ErroEsperado("Vários npjs encontrados")
