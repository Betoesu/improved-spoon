# -*- coding: utf-8 -*-
"""
Cadastro e atualização de NPJ a partir de publicações do STJ.
"""

import time
from typing import Any, Dict, Tuple

from DijurLib.api.base import post_api_navegador, put_api_navegador
from DijurLib.api.cadastro_npj import cadastro
from DijurLib.api.consulta import get_processos_npj
from DijurLib.api.npj import npj_dados_processo, npj_pessoas_processo, reativar_npj

from libs.distribuidor import DistribuidorAdvogados
from libs.erros import ErroEsperado
from libs.helper import check_partes, update_log

DEPENDENCIA_ALVO = 8553
UF_PADRAO_STJ = "DF"
CODIGO_TIPO_NUMERACAO_OUTROS = 3

URL_CADASTRO_NUMERO_CNJ = "https://juridico.intranet.bb.com.br/paj/resources/app/v1/processo/cadastro/numero/cnj"
URL_CADASTRO_NUMERO_OUTROS = "https://juridico.intranet.bb.com.br/paj/resources/app/v1/processo/cadastro/numero"
URL_CONSULTA_ADVOGADO = "https://juridico.intranet.bb.com.br/paj/resources/app/v0/advogado"
URL_VALIDACAO_MATERIA = "https://juridico.intranet.bb.com.br/paj/resources/app/v1/publicacao/validacao/materia/"
URL_DISTRIBUICAO_FUNCIONARIO = "https://juridico.intranet.bb.com.br/paj/resources/app/v0/distribuicao/processo/funcionario"


def _atualizar_cnj_npj(driver: Any, id_npj: str, cnj: str) -> None:
    """Atualiza o número CNJ (vinculado à UF DF) em um NPJ já cadastrado."""
    payload_uf = {
        "numeroConselhoNacionalJustica": cnj,
        "siglaUnidadeFederacao": UF_PADRAO_STJ,
    }
    post_api_navegador(driver, URL_CADASTRO_NUMERO_CNJ, payload_uf)

    payload_cnj = {
        "numeroProcesso": id_npj,
        "textoNumeroInventario": cnj,
    }
    cnj_api = put_api_navegador(driver, URL_CADASTRO_NUMERO_CNJ, payload_cnj)
    if not cnj_api or cnj_api.get("status") != "OK":
        raise ErroEsperado("Erro ao atualizar o CNJ")


def _atualizar_outros_npj(driver: Any, id_npj: str, outros: str) -> None:
    """Adiciona o número 'Outros' (código tipo 3) em um NPJ já cadastrado."""
    payload_outros = {
        "numeroProcesso": id_npj,
        "codigoTipoNumeracaoProcesso": CODIGO_TIPO_NUMERACAO_OUTROS,
        "textoNumeroExternoProcesso": outros,
    }
    outros_api = post_api_navegador(driver, URL_CADASTRO_NUMERO_OUTROS, payload_outros)
    if not outros_api or outros_api.get("status") != "OK":
        raise ErroEsperado("Erro ao atualizar o outros")


def _atualizar_advogado_npj(driver: Any, id_npj: str, advogado: str) -> None:
    """Busca o advogado no portal, valida a matéria e distribui o processo ao advogado."""
    advogado_payload = {"nome": advogado.upper()}
    advogado_api = post_api_navegador(driver, URL_CONSULTA_ADVOGADO, advogado_payload)
    if not advogado_api or advogado_api.get("status") != "OK":
        raise ErroEsperado("Erro ao buscar o advogado")
    advogado_data = advogado_api["data"][0]

    materia_api = post_api_navegador(driver, URL_VALIDACAO_MATERIA, id_npj)
    if materia_api.get("status") != "OK":
        raise ErroEsperado("Erro ao indicar a materia")

    indicar_payload = {
        "numeroProcesso": id_npj,
        "numeroAdvogado": advogado_data["numeroAdvogado"],
        "codigoTipoAdvogado": advogado_data["codigoTipoAdvogado"],
        "numeroCpfCnpjPessoaInteresseJuridico": advogado_data["cpfCnpj"],
        "chaveAdvogado": advogado_data["matricula"],
    }
    indicar_adv_api = post_api_navegador(driver, URL_DISTRIBUICAO_FUNCIONARIO, indicar_payload)
    if not indicar_adv_api or indicar_adv_api.get("status") != "OK":
        raise ErroEsperado("Erro ao atualizar o advogado")


def conferir_npj(
    driver: Any,
    dados_npj: Dict[str, Any],
    distribuidor_advogados: DistribuidorAdvogados,
    log_widget: Any,
) -> Tuple[str, bool, str]:
    """Confere existência de NPJ correspondente; reativa/atualiza ou cadastra novo."""
    npj_principal = dados_npj["npj_principal"]
    pesquisa = get_processos_npj(driver, npj_principal)

    if pesquisa["quantidade"] == 0:
        raise ErroEsperado(f"Nenhum npj encontrado para o npj {npj_principal}")

    lista_npj = pesquisa.get("listaOcorrencia", [])

    if lista_npj[0].get("textoEstadoProcesso") == "Excluido":
        raise ErroEsperado(f"NPJ Principal {lista_npj[0].get('numeroProcessoPrincipalSaida')}-000 excluído")

    baixado = bool(lista_npj[0].get("indicadorProcessoAtivo") == "N")
    advogado_principal_baixado = lista_npj[0].get("nomeAdvogadoProcesso", "")

    lista_npj = [p for p in lista_npj if p.get("textoEstadoProcesso") != "Excluído"]
    if any(p.get("indicadorProcessoConfidencial") == "S" for p in lista_npj):
        raise ErroEsperado("NPJ confidencial encontrado")

    lista_npj = [p for p in lista_npj if p.get("codigoPrefixoDependencia") == DEPENDENCIA_ALVO]
    publicacao = dados_npj.get("publicacao")

    for npj_cadastrado in lista_npj:
        id_npj = npj_cadastrado["numeroProcesso"]
        numeros = npj_dados_processo(driver, id_npj, ["numeros"])
        if numeros["publicacao"] == publicacao:
            update_log(log_widget, "NPJ já cadastrado, conferindo dados...")
            if baixado:
                npj_incidental = f"{npj_principal}000"
                update_log(log_widget, f"Reativando npj principal {npj_principal}...")
                reativacao_respose = reativar_npj(driver, "Agendar Incidental.", npj_incidental, npj_principal)

                if reativacao_respose["status"] != "OK":
                    raise ErroEsperado("Erro ao reativar o processo principal")

            id_npj, reativacao = npj_ja_cadastrado(
                driver,
                id_npj,
                npj_cadastrado,
                numeros,
                dados_npj,
                distribuidor_advogados,
                log_widget,
                principal_baixado=baixado,
            )
            return id_npj, True, reativacao
        time.sleep(0.2)

    polos_ativos, polos_passivos, polos_neutros = dados_npj.get("polos")
    polos_cadastrados = npj_pessoas_processo(driver, npj_principal)
    partes_cadastradas = (
        polos_cadastrados.get("ativos", [])
        + polos_cadastrados.get("passivos", [])
        + polos_cadastrados.get("neutros", [])
    )

    confere_partes = (
        check_partes(partes_cadastradas, polos_ativos)
        and check_partes(partes_cadastradas, polos_passivos)
        and (not polos_neutros or check_partes(partes_cadastradas, polos_neutros))
    )

    if not confere_partes:
        print("Breakpoint: Partes não conferem")
        raise ErroEsperado("Partes não identificadas")

    if baixado:
        npj_incidental = f"{npj_principal}000"
        reativacao_respose = reativar_npj(driver, "Cadastrar Incidental.", npj_incidental, npj_principal)

        if reativacao_respose["status"] != "OK":
            raise ErroEsperado("Erro ao reativar o processo principal")

        # Caso tenha que reativar o processo: se o advogado do NPJ baixado estiver
        # na lista dos advogados, mantém; se não estiver, aciona a lista.
        advogado, manteve_advogado = distribuidor_advogados.definir_advogado_reativacao(
            advogado_principal_baixado
        )
        if manteve_advogado:
            update_log(
                log_widget,
                f"Processo reativado ({npj_principal}) | Mantendo advogado da lista: {advogado}",
            )
        else:
            update_log(
                log_widget,
                f"Processo reativado ({npj_principal}) | Advogado anterior ('{advogado_principal_baixado}') "
                f"fora da lista -> Advogado da fila: {advogado}",
            )
    else:
        # Nova distribuição sem reativação: seleciona o próximo advogado da fila.
        advogado = distribuidor_advogados.proximo_advogado()
        update_log(
            log_widget,
            f"Cadastrando npj incidental {npj_principal} | Advogado da fila: {advogado}",
        )

    dados_npj["advogado"] = advogado
    try:
        hora = time.strftime("%H:%M:%S")
        numeroProcessoCriado, conferePartes = cadastro(
            driver,
            npj_principal,
            dados_npj.get("polo"),
            dados_npj.get("autuacao"),
            dados_npj.get("cnj"),
            publicacao,
            dados_npj.get("polos"),
            dados_npj.get("tramitacao"),
            advogado,
            dados_npj.get("processo"),
            "STJ",
            dados_npj.get("outros"),
        )
    except Exception as e:
        raise ErroEsperado(f"Cadastro incompleto: {e}")

    return numeroProcessoCriado, conferePartes, hora


def npj_ja_cadastrado(
    driver: Any,
    id_npj: str,
    npj_cadastrado: Dict[str, Any],
    numeros: Dict[str, Any],
    dados_npj: Dict[str, Any],
    distribuidor_advogados: DistribuidorAdvogados,
    log_widget: Any,
    principal_baixado: bool = False,
) -> Tuple[str, str]:
    """
    Verifica se o NPJ já foi cadastrado e atualiza CNJ/Outros se necessário.

    Se o processo precisou ser reativado (`situacao == 'N'` ou `principal_baixado`),
    verifica se o advogado do NPJ baixado está na lista de advogados:
    - Se estiver na lista, mantém o advogado atual.
    - Se não estiver na lista, aciona a fila (`proximo_advogado`) e atualiza o NPJ.
    """
    situacao = npj_cadastrado["indicadorProcessoAtivo"]
    advogado_cadastrado = npj_cadastrado.get("nomeAdvogadoProcesso", "")
    cnj_cadastrado = numeros["cnj"]
    outros_cadastrado = numeros["outros"]

    cnj = dados_npj["cnj"]
    outros = dados_npj["outros"]

    if situacao == "N":
        variacao = str(npj_cadastrado["numeroOrdemVariacaoSaida"]).zfill(3)
        npj_incidental = f"{dados_npj['npj_principal']}{variacao}"
        reativacao_respose = reativar_npj(driver, "Agendar Publicação.", npj_incidental, id_npj)

        if reativacao_respose["status"] != "OK":
            raise ErroEsperado("Erro ao reativar o processo incidental")

        reativacao = "Reativado"
    else:
        reativacao = "Já cadastrado"

    if cnj != cnj_cadastrado:
        _atualizar_cnj_npj(driver, id_npj, cnj)

    if outros not in outros_cadastrado:
        _atualizar_outros_npj(driver, id_npj, outros)

    if situacao == "N" or principal_baixado:
        advogado_definido, manteve_advogado = distribuidor_advogados.definir_advogado_reativacao(
            advogado_cadastrado
        )
        if manteve_advogado:
            update_log(
                log_widget,
                f"NPJ reativado: advogado '{advogado_cadastrado}' está na lista (mantido).",
            )
        else:
            update_log(
                log_widget,
                f"NPJ reativado: advogado '{advogado_cadastrado}' fora da lista -> "
                f"Acionando fila: {advogado_definido}",
            )
            _atualizar_advogado_npj(driver, id_npj, advogado_definido)

    return id_npj, reativacao
