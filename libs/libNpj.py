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

from libs.distribuidor import DistribuidorAdvogados
from libs.erros import ErroEsperado
from libs.helper import check_partes, update_log

DEPENDENCIA_ALVO = 8553
UF_PADRAO_STJ = "DF"
CODIGO_TIPO_NUMERACAO_OUTROS = 3

# Na API de Terceiros (listarPessoasProcesso/{id}/4/0), o Portal Jurídico identifica:
#   - codigoTipoRelacionamentoPessoaProcesso = 36 -> Advogado Compartilhado
#   - codigoTipoRelacionamentoPessoaProcesso = 32 -> Terceiro / Órgão / Interessado
#   - codigoTipoRelacionamentoPessoaProcesso = 7  -> Advogado Adverso
CODIGO_RELACIONAMENTO_ADVOGADO_COMPARTILHADO: Optional[int] = 36

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
    ao tipo 'Advogado Compartilhado':
        1) Pelo código numérico oficial da API (`codigoTipoRelacionamentoPessoaProcesso == 36`);
        2) Ou pelo campo textual (`textoTipoRelacionamentoPessoaProcesso == 'ADVOGADO COMPARTILHADO'`).
    """
    if CODIGO_RELACIONAMENTO_ADVOGADO_COMPARTILHADO is not None:
        cod_rel = item.get("codigoTipoRelacionamentoPessoaProcesso")
        try:
            if cod_rel is not None and int(cod_rel) == int(CODIGO_RELACIONAMENTO_ADVOGADO_COMPARTILHADO):
                return True
        except (ValueError, TypeError):
            pass

    texto_tipo = str(item.get("textoTipoRelacionamentoPessoaProcesso") or "").strip().upper()
    if texto_tipo:
        return (
            texto_tipo == "ADVOGADO COMPARTILHADO"
            or "ADVOGADO COMPARTILHAD" in texto_tipo
        )

    return False


def _obter_advogados_compartilhados_principal(driver: Any, id_npj_principal: Any) -> List[str]:
    """
    Consulta a aba 'Pessoas do Processo -> Terceiros' (polo 4) do NPJ Principal (variação 000)
    via API do Portal Jurídico (`v2/pessoas/listarPessoasProcesso/{idNpj}/4/{pos}` com fallback
    para `v1`), percorrendo a paginação quando `indicadorContinuacaoPesquisa == 'S'`, e retorna
    os nomes (`nomeRazaoSocialClientePessoa`) cujo tipo é 'Advogado Compartilhado' (código 36).
    """
    advogados_compartilhados: List[str] = []
    vistos = set()

    try:
        posicao = 0
        paginas_consultadas = 0

        while paginas_consultadas < 20:
            url_v2 = (
                f"https://juridico.intranet.bb.com.br/paj/resources/app/v2/pessoas/"
                f"listarPessoasProcesso/{id_npj_principal}/4/{posicao}"
            )
            response = get_api_navegador(driver, url_v2)
            if not response or response.get("statusCode") != 200:
                url_v1 = (
                    f"https://juridico.intranet.bb.com.br/paj/resources/app/v1/pessoas/"
                    f"listarPessoasProcesso/{id_npj_principal}/4/{posicao}"
                )
                response = get_api_navegador(driver, url_v1)

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

            indicador_continuacao = str(
                data.get("indicadorContinuacaoPesquisa")
                or data.get("indicadorContinuacao")
                or ""
            ).upper()
            if indicador_continuacao == "S" and len(ocorrencias) >= 10:
                posicao = 11 if posicao == 0 else posicao + 10
                paginas_consultadas += 1
            else:
                break
    except Exception:
        return advogados_compartilhados

    return advogados_compartilhados


def conferir_npj(
    driver: Any,
    dados_npj: Dict[str, Any],
    distribuidor_advogados: DistribuidorAdvogados,
    log_widget: Any,
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

    lista_npj_8553 = [p for p in lista_npj if p.get("codigoPrefixoDependencia") == DEPENDENCIA_ALVO]
    variacoes_8553 = [
        dict(p) for p in lista_npj_8553 if int(p.get("numeroOrdemVariacaoSaida", 0)) > 0
    ]
    publicacao = dados_npj.get("publicacao")

    for npj_cadastrado in lista_npj_8553:
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
                id_npj_principal=id_npj_principal,
                variacoes_8553=variacoes_8553,
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

    # --------------------------------------------------------------------------
    # DISTRIBUIÇÃO DE NOVA VARIAÇÃO:
    # 0) Verifica condução compartilhada no NPJ principal (000) -> Advogado ADEST
    # 1) Se não houver, verifica variações da 8553 (1.1 única, 1.2 maior p/ menor, 1.3 sem variação)
    # --------------------------------------------------------------------------
    advogados_compartilhados = _obter_advogados_compartilhados_principal(
        driver, id_npj_principal
    )
    advogado, motivo_distribuicao = distribuidor_advogados.definir_advogado_nova_variacao(
        advogados_compartilhados,
        variacoes_8553,
        npj_principal=str(npj_principal),
    )
    update_log(
        log_widget,
        f"Cadastrando nova variação para NPJ {npj_principal} | {motivo_distribuicao}",
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

    distribuidor_advogados.registrar_publicacao_advogado(
        advogado=advogado,
        tipo="Nova Variação",
        publicacao=str(publicacao),
        npj=f"{npj_principal[:4]}/{npj_principal[4:]} (ID {numeroProcessoCriado})",
        regra=motivo_distribuicao,
        id_publicacao=str(dados_npj.get("id_publicacao", "")),
        advogado_legado=str(dados_npj.get("advogado_legado", "")),
        outros=str(dados_npj.get("outros", "")),
    )

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
    id_npj_principal: Optional[Any] = None,
    variacoes_8553: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, str]:
    """
    Verifica se o NPJ já foi cadastrado e atualiza CNJ/Outros se necessário.

    Se o processo precisou ser reativado (`situacao == 'N'` ou `principal_baixado`),
    aplica a Regra de Redistribuição de Variações Reativadas:
    1. Se o advogado da variação estiver em ADEST ou TODOS da ADREC -> mantém (reativa e distribui).
    2. Se não estiver -> verifica Advogado Compartilhado no NPJ 000 (ADEST) -> Variações da 8553
       (1.1 única, 1.2 maior p/ menor, 1.3 sem variação) -> Fila dos 6 da ADREC.
    3. Se a publicação reativada foi para um advogado dos 6 da ADREC e o próximo da fila é ele,
       pula para o próximo nome da fila para não repetir 2x seguidas.
    """
    situacao = npj_cadastrado.get("indicadorProcessoAtivo")
    advogado_cadastrado = str(npj_cadastrado.get("nomeAdvogadoProcesso") or "")
    cnj_cadastrado = numeros.get("cnj")
    outros_cadastrado = numeros.get("outros") or []

    cnj = dados_npj["cnj"]
    outros = dados_npj["outros"]
    variacao = str(npj_cadastrado.get("numeroOrdemVariacaoSaida", 0)).zfill(3)
    npj_principal_str = str(dados_npj["npj_principal"])
    mascara_var = f"{npj_principal_str[:4]}/{npj_principal_str[4:]}-{variacao}"

    if situacao == "N":
        npj_incidental = f"{npj_principal_str}{variacao}"
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
        # Só consulta a API de Terceiros do NPJ 000 caso o advogado atual não esteja em ADEST/TODOS ADREC
        advogados_compartilhados: List[str] = []
        if (
            distribuidor_advogados.buscar_na_adest(advogado_cadastrado) is None
            and distribuidor_advogados.buscar_na_adrec_todos(advogado_cadastrado) is None
        ):
            alvo_principal = id_npj_principal or dados_npj["npj_principal"]
            advogados_compartilhados = _obter_advogados_compartilhados_principal(
                driver, alvo_principal
            )

        advogado_definido, manteve_advogado, motivo = (
            distribuidor_advogados.definir_advogado_reativacao(
                advogado_npj_baixado=advogado_cadastrado,
                advogados_compartilhados_principal=advogados_compartilhados,
                variacoes_8553=variacoes_8553 or [],
                npj_principal=npj_principal_str,
                variacao_reativada=variacao,
            )
        )
        dados_npj["advogado"] = advogado_definido

        if manteve_advogado:
            update_log(log_widget, f"NPJ reativado ({mascara_var}) | {motivo}")
        else:
            update_log(log_widget, f"NPJ reativado ({mascara_var}) | {motivo}")
            _atualizar_advogado_npj(driver, id_npj, advogado_definido)

        distribuidor_advogados.registrar_publicacao_advogado(
            advogado=advogado_definido,
            tipo="Reativada",
            publicacao=str(dados_npj.get("publicacao", "")),
            npj=mascara_var,
            regra=motivo,
            id_publicacao=str(dados_npj.get("id_publicacao", "")),
            advogado_legado=str(dados_npj.get("advogado_legado", "")),
            outros=str(outros),
        )
    else:
        dados_npj["advogado"] = distribuidor_advogados.padronizar_nome_advogado(
            advogado_cadastrado
        )
        distribuidor_advogados.registrar_publicacao_advogado(
            advogado=advogado_cadastrado,
            tipo="Já Cadastrada",
            publicacao=str(dados_npj.get("publicacao", "")),
            npj=mascara_var,
            regra="Regra 3.0 (Variação já cadastrada e ativa | Advogado atual mantido)",
            id_publicacao=str(dados_npj.get("id_publicacao", "")),
            advogado_legado=str(dados_npj.get("advogado_legado", "")),
            outros=str(outros),
        )

    return id_npj, reativacao
