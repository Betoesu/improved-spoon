# -*- coding: utf-8 -*-
"""
Cadastro e atualização de NPJ a partir de publicações do STJ.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from DijurLib.api.base import get_api_navegador, post_api_navegador, put_api_navegador
from DijurLib.api.cadastro_npj import cadastro
from DijurLib.api.consulta import get_processos_npj
from DijurLib.api.npj import npj_dados_processo, npj_pessoas_processo, reativar_npj

from libs.distribuidor import DistribuidorAdvogados, criar_passos_vazios
from libs.erros import ErroEsperado
from libs.helper import check_partes, update_log

DEPENDENCIA_ALVO = 8553
UF_PADRAO_STJ = "DF"
CODIGO_TIPO_NUMERACAO_OUTROS = 3

CODIGO_RELACIONAMENTO_ADVOGADO_COMPARTILHADO: Optional[int] = None

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
    nome_advogado = str(advogado or "").strip().upper()
    if not nome_advogado:
        return

    advogado_payload: Dict[str, Any] = {
        "codigoTipoAdvogado": 2,
        "nome": nome_advogado,
    }
    advogado_api = post_api_navegador(driver, URL_CONSULTA_ADVOGADO, advogado_payload)

    if (
        not advogado_api
        or advogado_api.get("status") != "OK"
        or not advogado_api.get("data")
    ):
        advogado_api = post_api_navegador(
            driver,
            URL_CONSULTA_ADVOGADO,
            {"nome": nome_advogado},
        )

    if not advogado_api or advogado_api.get("status") != "OK":
        raise ErroEsperado("Erro ao buscar o advogado")

    lista_advogados = advogado_api.get("data") or []
    if not lista_advogados:
        # Caso o advogado configurado não retorne registros ativos no portal (data=[]),
        # mantém a variação 8553 já cadastrada como está para permitir a indicação da publicação.
        return

    advogado_data = lista_advogados[0]

    materia_api = post_api_navegador(driver, URL_VALIDACAO_MATERIA, id_npj)
    if not materia_api or materia_api.get("status") != "OK":
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


def _eh_item_advogado_compartilhado(item: Dict[str, Any]) -> bool:
    """
    Verifica se um registro da aba 'Pessoas do Processo -> Terceiros' corresponde
    ao tipo 'Advogado Compartilhado', utilizando o campo oficial do Portal Jurídico
    `textoTipoRelacionamentoPessoaProcesso` (coluna 'Tipo' da tabela de Terceiros).
    """
    texto_tipo = str(item.get("textoTipoRelacionamentoPessoaProcesso") or "").strip().upper()
    if texto_tipo:
        return (
            texto_tipo == "ADVOGADO COMPARTILHADO"
            or "ADVOGADO COMPARTILHAD" in texto_tipo
        )

    if CODIGO_RELACIONAMENTO_ADVOGADO_COMPARTILHADO is not None:
        if item.get("codigoTipoRelacionamentoPessoaProcesso") == CODIGO_RELACIONAMENTO_ADVOGADO_COMPARTILHADO:
            return True

    return False


def _obter_advogados_compartilhados_principal(driver: Any, id_npj_principal: Any) -> List[str]:
    """
    Consulta (somente leitura) a aba 'Pessoas do Processo -> Terceiros' (polo 4) do NPJ Principal
    (variação 000) via API do Portal Jurídico (`listarPessoasProcesso/{idNpj}/4/{pos}`),
    para alimentar a simulação em segundo plano da `versao_lista_advogados`.
    """
    advogados_compartilhados: List[str] = []
    vistos = set()

    try:
        posicao = 0
        paginas_consultadas = 0

        while paginas_consultadas < 20:
            url_terceiros = (
                f"https://juridico.intranet.bb.com.br/paj/resources/app/v1/pessoas/"
                f"listarPessoasProcesso/{id_npj_principal}/4/{posicao}"
            )
            response = get_api_navegador(driver, url_terceiros)
            if not response or response.get("statusCode") != 200:
                break

            data = response.get("data") or {}
            ocorrencias = data.get("listaOcorrencia") or []
            if not ocorrencias:
                break

            for item in ocorrencias:
                if not isinstance(item, dict):
                    continue
                if _eh_item_advogado_compartilhado(item):
                    nome = (
                        item.get("nomeRazaoSocialClientePessoa")
                        or item.get("nomePessoa")
                        or item.get("nomeRazaoSocialAdvogado")
                        or ""
                    ).strip()
                    if nome and nome.upper() not in vistos:
                        vistos.add(nome.upper())
                        advogados_compartilhados.append(nome)

            indicador_continuacao = str(data.get("indicadorContinuacao") or "").upper()
            if indicador_continuacao == "S" and len(ocorrencias) >= 10:
                posicao = 11 if posicao == 0 else posicao + 10
                paginas_consultadas += 1
            else:
                break
    except Exception:
        return advogados_compartilhados

    return advogados_compartilhados


def _simular_lista_advogados_ja_cadastrado(
    driver: Any,
    distribuidor_advogados: DistribuidorAdvogados,
    situacao_original: str,
    advogado_cadastrado_original: str,
    variacao_str: str,
    npj_principal_str: str,
    principal_baixado: bool,
    id_npj_principal: Any,
    variacoes_8553: List[Dict[str, Any]],
    dados_npj: Dict[str, Any],
) -> None:
    """
    Executa apenas em memória (shadow mode) o que a `versao_lista_advogados` faria
    para um NPJ já cadastrado (Ativo ou Reativado), sem realizar nenhuma alteração no portal.
    """
    try:
        mascara_var = f"{npj_principal_str[:4]}/{npj_principal_str[4:]}-{variacao_str}"
        id_pub = str(dados_npj.get("id_publicacao", ""))
        cnj_pub = str(dados_npj.get("publicacao", ""))

        if situacao_original == "N" or principal_baixado:
            advogados_compartilhados: List[str] = []
            if (
                distribuidor_advogados.buscar_na_adest(advogado_cadastrado_original) is None
                and distribuidor_advogados.buscar_na_adrec_todos(advogado_cadastrado_original) is None
            ):
                alvo_principal = id_npj_principal or npj_principal_str
                advogados_compartilhados = _obter_advogados_compartilhados_principal(
                    driver, alvo_principal
                )

            advogado_simulado, _, motivo, passos = (
                distribuidor_advogados.definir_advogado_reativacao(
                    advogado_npj_baixado=advogado_cadastrado_original,
                    advogados_compartilhados_principal=advogados_compartilhados,
                    variacoes_8553=variacoes_8553,
                )
            )
            distribuidor_advogados.registrar_publicacao_advogado(
                advogado=advogado_simulado,
                tipo="Reativada",
                publicacao=cnj_pub,
                npj=mascara_var,
                regra=motivo,
                id_publicacao=id_pub,
                passos=passos,
            )
        else:
            passos = criar_passos_vazios()
            passos["Variação Já Cadastrada e Ativa?"] = "Sim"
            distribuidor_advogados.registrar_publicacao_advogado(
                advogado=advogado_cadastrado_original,
                tipo="Já Cadastrada",
                publicacao=cnj_pub,
                npj=mascara_var,
                regra="Variação já cadastrada e ativa (advogado mantido)",
                id_publicacao=id_pub,
                passos=passos,
            )
    except Exception:
        pass


def _simular_lista_advogados_nova_variacao(
    driver: Any,
    distribuidor_advogados: DistribuidorAdvogados,
    npj_principal_str: str,
    id_npj_principal: Any,
    numero_processo_criado: Any,
    variacoes_8553: List[Dict[str, Any]],
    dados_npj: Dict[str, Any],
) -> None:
    """
    Executa apenas em memória (shadow mode) o que a `versao_lista_advogados` faria
    para uma Nova Variação, sem realizar nenhuma alteração na distribuição real do portal.
    """
    try:
        id_pub = str(dados_npj.get("id_publicacao", ""))
        cnj_pub = str(dados_npj.get("publicacao", ""))
        advogados_compartilhados = _obter_advogados_compartilhados_principal(
            driver, id_npj_principal
        )
        advogado_simulado, motivo_distribuicao, passos = (
            distribuidor_advogados.definir_advogado_nova_variacao(
                advogados_compartilhados,
                variacoes_8553,
            )
        )
        distribuidor_advogados.registrar_publicacao_advogado(
            advogado=advogado_simulado,
            tipo="Nova Variação",
            publicacao=cnj_pub,
            npj=f"{npj_principal_str[:4]}/{npj_principal_str[4:]} (ID {numero_processo_criado})",
            regra=motivo_distribuicao,
            id_publicacao=id_pub,
            passos=passos,
        )
    except Exception:
        pass


def conferir_npj(
    driver: Any,
    dados_npj: Dict[str, Any],
    log_widget: Any,
    distribuidor_advogados: Optional[DistribuidorAdvogados] = None,
) -> Tuple[str, bool, str]:
    """Confere existência de NPJ correspondente; reativa/atualiza ou cadastra novo."""
    npj_principal = dados_npj["npj_principal"]
    pesquisa = get_processos_npj(driver, npj_principal)

    lista_npj = pesquisa.get("listaOcorrencia", [])
    if pesquisa["quantidade"] == 0 or not lista_npj:
        raise ErroEsperado(f"Nenhum npj encontrado para o npj {npj_principal}")

    if lista_npj[0].get("textoEstadoProcesso") == "Excluido":
        raise ErroEsperado(f"NPJ Principal {lista_npj[0].get('numeroProcessoPrincipalSaida')}-000 excluído")

    id_npj_principal = lista_npj[0].get("numeroProcesso", npj_principal)
    baixado = bool(lista_npj[0].get("indicadorProcessoAtivo") == "N")

    lista_npj = [p for p in lista_npj if p.get("textoEstadoProcesso") != "Excluído"]
    if any(p.get("indicadorProcessoConfidencial") == "S" for p in lista_npj):
        raise ErroEsperado("NPJ confidencial encontrado")

    lista_npj = [p for p in lista_npj if p.get("codigoPrefixoDependencia") == DEPENDENCIA_ALVO]
    variacoes_8553 = [
        dict(p) for p in lista_npj if int(p.get("numeroOrdemVariacaoSaida", 0)) > 0
    ]
    publicacao = dados_npj.get("publicacao")

    for npj_cadastrado in lista_npj:
        id_npj = npj_cadastrado["numeroProcesso"]
        numeros = npj_dados_processo(driver, id_npj, ["numeros"])
        if numeros["publicacao"] == publicacao:
            situacao_original = str(npj_cadastrado.get("indicadorProcessoAtivo", ""))
            advogado_cadastrado_original = str(npj_cadastrado.get("nomeAdvogadoProcesso", ""))
            variacao_str = str(npj_cadastrado.get("numeroOrdemVariacaoSaida", 0)).zfill(3)

            update_log(log_widget, "NPJ já cadastrado, conferindo dados...")
            if baixado:
                npj_incidental = f"{npj_principal}000"
                update_log(log_widget, f"Reativando npj principal {npj_principal}...")
                reativacao_respose = reativar_npj(driver, "Agendar Incidental.", npj_incidental, npj_principal)

                if reativacao_respose["status"] != "OK":
                    raise ErroEsperado("Erro ao reativar o processo principal")

            id_npj, reativacao = npj_ja_cadastrado(driver, id_npj, npj_cadastrado, numeros, dados_npj)

            if distribuidor_advogados is not None:
                _simular_lista_advogados_ja_cadastrado(
                    driver=driver,
                    distribuidor_advogados=distribuidor_advogados,
                    situacao_original=situacao_original,
                    advogado_cadastrado_original=advogado_cadastrado_original,
                    variacao_str=variacao_str,
                    npj_principal_str=str(npj_principal),
                    principal_baixado=baixado,
                    id_npj_principal=id_npj_principal,
                    variacoes_8553=variacoes_8553,
                    dados_npj=dados_npj,
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

    update_log(log_widget, f"Cadastrando npj incidental {npj_principal}...")
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
            dados_npj.get("advogado"),
            dados_npj.get("processo"),
            "STJ",
            dados_npj.get("outros"),
        )
    except Exception as e:
        raise ErroEsperado(f"Cadastro incompleto: {e}")

    if distribuidor_advogados is not None:
        _simular_lista_advogados_nova_variacao(
            driver=driver,
            distribuidor_advogados=distribuidor_advogados,
            npj_principal_str=str(npj_principal),
            id_npj_principal=id_npj_principal,
            numero_processo_criado=numeroProcessoCriado,
            variacoes_8553=variacoes_8553,
            dados_npj=dados_npj,
        )

    return numeroProcessoCriado, conferePartes, hora


def npj_ja_cadastrado(
    driver: Any,
    id_npj: str,
    npj_cadastrado: Dict[str, Any],
    numeros: Dict[str, Any],
    dados_npj: Dict[str, Any],
) -> Tuple[str, str]:
    """Verifica se o npj já foi cadastrado e atualiza as informações se necessário."""
    situacao = npj_cadastrado.get("indicadorProcessoAtivo")
    advogado_cadastrado = str(npj_cadastrado.get("nomeAdvogadoProcesso") or "")
    cnj_cadastrado = numeros.get("cnj")
    outros_cadastrado = numeros.get("outros") or []

    cnj = dados_npj["cnj"]
    outros = dados_npj["outros"]
    advogado = str(dados_npj.get("advogado") or "")

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

    if advogado_cadastrado not in advogado:
        _atualizar_advogado_npj(driver, id_npj, advogado)

    return id_npj, reativacao
