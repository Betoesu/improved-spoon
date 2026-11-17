# -*- coding: utf-8 -*-
"""
Script simples de teste (CLI) para validar a Regra 1.0 (Condução Compartilhada /
Advogado Compartilhado em Terceiros do NPJ 000) na `versao_lista_advogados`.

Uso:
    1) Preencha a lista `NPJS_TESTE` abaixo OU passe os NPJs por linha de comando
       OU digite/cole no terminal quando solicitado.
    2) Execute: `python teste_regra_1_0.py`
    3) Faça o login manual no navegador que será aberto.
    4) Veja para cada NPJ 000:
       - Todos os registros em "Pessoas do Processo -> Terceiros" do NPJ 000
       - Quais foram detectados como "Advogado Compartilhado"
       - Quais estão na lista `advogados_adest` (Regra 1.0)
       - Variações 8553 encontradas no NPJ
       - O que a automação faria em caso de NOVA DISTRIBUIÇÃO
       - O que a automação faria em caso de REATIVAÇÃO
"""

import copy
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# Garante que `versao_lista_advogados/libs` tenha prioridade sobre `STJ/libs` e inclui `STJ` para `DijurLib`
DIR_ATUAL = os.path.dirname(os.path.abspath(__file__))
DIR_PAI = os.path.dirname(DIR_ATUAL)
if DIR_PAI not in sys.path:
    sys.path.append(DIR_PAI)
if DIR_ATUAL in sys.path:
    sys.path.remove(DIR_ATUAL)
sys.path.insert(0, DIR_ATUAL)

from DijurLib.api.base import get_api_navegador
from DijurLib.api.consulta import get_processos_npj
from DijurLib.portal.login import login_manual
from DijurLib.utils.navegador import iniciar_navegador

from libs.config import DEFAULT_CONFIG, obter_listas_advogados
from libs.distribuidor import DistribuidorAdvogados
from libs.helper import ensure_base_url
from libs.libNpj import (
    DEPENDENCIA_ALVO,
    _eh_item_advogado_compartilhado,
    _obter_advogados_compartilhados_principal,
)

# ------------------------------------------------------------------------------
# Opcional: coloque aqui os NPJs 000 fixos para testar direto, ex:
# NPJS_TESTE = ["2024/0010001-000", "20250019564000"]
# Se deixar vazio, o programa pede no terminal (ou aceita via argumentos CLI).
# ------------------------------------------------------------------------------
NPJS_TESTE: List[str] = []


def carregar_listas_config() -> Dict[str, List[str]]:
    """Carrega as listas de advogados do stj_config.json (se existir) ou DEFAULT_CONFIG."""
    candidatos = [
        os.path.join(os.getcwd(), "stj_config.json"),
        os.path.join(DIR_ATUAL, "stj_config.json"),
        os.path.join(DIR_PAI, "stj_config.json"),
    ]
    cfg: Dict[str, Any] = dict(DEFAULT_CONFIG)
    for caminho in candidatos:
        if os.path.exists(caminho):
            try:
                with open(caminho, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                print(f"[Config] Usando '{caminho}'")
                break
            except Exception as e:
                print(f"[Config] Aviso: falha ao ler '{caminho}' ({e}). Usando DEFAULT_CONFIG.")
    return obter_listas_advogados(cfg)


def normalizar_npj_principal(entrada: str) -> Tuple[str, str]:
    """
    Normaliza um NPJ informado (ex: '2024/0010001-000', '20240010001000', '2024/10001-000',
    '20240010001') retornando `(npj_principal_11_digitos, mascara_000)`.
    """
    texto = str(entrada or "").strip()
    if not texto:
        raise ValueError("NPJ vazio.")

    # Trata formato com barra, ex: 2024/10001-000 ou 2024/0010001
    if "/" in texto:
        ano, resto = texto.split("/", 1)
        ano_dig = re.sub(r"\D", "", ano)
        if "-" in resto:
            num_proc, _ = resto.split("-", 1)
        else:
            num_proc = resto
        num_dig = re.sub(r"\D", "", num_proc)
        if len(num_dig) == 10 and texto.count("-") == 0:
            # Caso raro tipo 2024/0010001000
            num_dig = num_dig[:7]
        npj_principal = f"{ano_dig.zfill(4)}{num_dig.zfill(7)}"
    else:
        digitos = re.sub(r"\D", "", texto)
        if len(digitos) >= 14:
            npj_principal = digitos[:11]
        elif len(digitos) == 11:
            npj_principal = digitos
        elif len(digitos) > 4:
            npj_principal = f"{digitos[:4]}{digitos[4:].zfill(7)}"[:11]
        else:
            raise ValueError(f"Formato de NPJ inválido: '{entrada}'")

    if len(npj_principal) != 11 or not npj_principal.isdigit():
        raise ValueError(f"Não foi possível extrair 11 dígitos do NPJ: '{entrada}'")

    mascara_000 = f"{npj_principal[:4]}/{npj_principal[4:]}-000"
    return npj_principal, mascara_000


def listar_todos_terceiros(driver: Any, id_npj_principal: Any) -> List[Dict[str, Any]]:
    """
    Consulta TODAS as ocorrências da aba 'Pessoas do Processo -> Terceiros' (polo 4)
    para diagnóstico completo (mostra advogados comuns e advogados compartilhados).
    """
    todos: List[Dict[str, Any]] = []
    posicao = 0
    paginas = 0

    while paginas < 20:
        url = (
            f"https://juridico.intranet.bb.com.br/paj/resources/app/v1/pessoas/"
            f"listarPessoasProcesso/{id_npj_principal}/4/{posicao}"
        )
        response = get_api_navegador(driver, url)
        if not response or response.get("statusCode") != 200:
            break

        data = response.get("data") or {}
        ocorrencias = data.get("listaOcorrencia") or []
        if not ocorrencias:
            break

        for item in ocorrencias:
            if isinstance(item, dict):
                todos.append(item)

        indicador_continuacao = str(data.get("indicadorContinuacao") or "").upper()
        if indicador_continuacao == "S" and len(ocorrencias) >= 10:
            posicao = 11 if posicao == 0 else posicao + 10
            paginas += 1
        else:
            break

    return todos


def classificar_advogado_nas_listas(
    distribuidor: DistribuidorAdvogados, nome: Optional[str]
) -> str:
    """Retorna em quais listas o nome aparece."""
    if not nome or not str(nome).strip():
        return "Sem nome"

    tags: List[str] = []
    if distribuidor.buscar_na_adest(nome):
        tags.append("ADEST")
    if distribuidor.buscar_na_adrec_6(nome):
        tags.append("6_ADREC")
    if distribuidor.buscar_na_adrec_todos(nome):
        tags.append("TODOS_ADREC")

    return ", ".join(tags) if tags else "FORA DAS LISTAS"


def analisar_npj(
    driver: Any,
    entrada_npj: str,
    distribuidor_base: DistribuidorAdvogados,
) -> None:
    """Consulta o NPJ 000 no portal e simula Nova Distribuição e Reativação."""
    print("\n" + "=" * 90)
    print(f"ENTRADA: {entrada_npj}")

    try:
        npj_principal, mascara_000 = normalizar_npj_principal(entrada_npj)
    except ValueError as e:
        print(f"  [ERRO] {e}")
        return

    print(f"NPJ 000: {mascara_000} (principal={npj_principal})")

    pesquisa = get_processos_npj(driver, npj_principal)
    lista_npj = pesquisa.get("listaOcorrencia", [])
    if pesquisa.get("quantidade", 0) == 0 or not lista_npj:
        print(f"  [ERRO] Nenhum NPJ encontrado na consulta para {npj_principal}.")
        return

    # Localiza o registro do 000 (mesma lógica do libNpj: lista_npj[0], priorizando variação 0 se existir)
    item_000 = next(
        (p for p in lista_npj if int(p.get("numeroOrdemVariacaoSaida", -1)) == 0),
        lista_npj[0],
    )
    id_npj_principal = item_000.get("numeroProcesso", npj_principal)
    estado_000 = item_000.get("textoEstadoProcesso", "?")
    ativo_000 = item_000.get("indicadorProcessoAtivo", "?")
    dep_000 = item_000.get("codigoPrefixoDependencia", "?")
    adv_000 = str(item_000.get("nomeAdvogadoProcesso") or "").strip()

    print(
        f"  ID Interno 000 : {id_npj_principal} | Dep: {dep_000} | "
        f"Ativo: {ativo_000} ({estado_000}) | Advogado no 000: '{adv_000 or 'Nenhum'}' "
        f"[{classificar_advogado_nas_listas(distribuidor_base, adv_000)}]"
    )

    if item_000.get("textoEstadoProcesso") == "Excluido":
        print("  [AVISO] NPJ Principal está marcado como 'Excluido'.")

    # Filtra variações da 8553 exatamente como em libs/libNpj.py
    lista_valida = [p for p in lista_npj if p.get("textoEstadoProcesso") != "Excluído"]
    lista_npj_8553 = [
        p for p in lista_valida if p.get("codigoPrefixoDependencia") == DEPENDENCIA_ALVO
    ]
    variacoes_8553 = [
        dict(p) for p in lista_npj_8553 if int(p.get("numeroOrdemVariacaoSaida", 0)) > 0
    ]

    # 1) Inspeção completa de Terceiros (polo 4) no NPJ 000
    terceiros_raw = listar_todos_terceiros(driver, id_npj_principal)
    advogados_compartilhados = _obter_advogados_compartilhados_principal(
        driver, id_npj_principal
    )

    print("\n  [TERCEIROS DO NPJ 000 (polo 4)]")
    if not terceiros_raw:
        print("    (Nenhum registro retornado na aba Terceiros)")
    else:
        for i, item in enumerate(terceiros_raw, start=1):
            nome = (
                item.get("nomeRazaoSocialClientePessoa")
                or item.get("nomePessoa")
                or item.get("nomeRazaoSocialAdvogado")
                or "(Sem nome)"
            ).strip()
            tipo_rel = item.get("textoTipoRelacionamentoPessoaProcesso") or "(sem textoTipoRelacionamentoPessoaProcesso)"
            desc_rel = item.get("textoDescricaoRelacao") or ""
            cod_rel = item.get("codigoTipoRelacionamentoPessoaProcesso")
            eh_comp = _eh_item_advogado_compartilhado(item)
            listas_tag = classificar_advogado_nas_listas(distribuidor_base, nome)

            extra_cod = f" | codTipoRel={cod_rel}" if cod_rel is not None else ""
            extra_desc = f" | desc='{desc_rel}'" if desc_rel else ""
            marca_comp = "SIM -> DETECTADO COMO ADV. COMPARTILHADO" if eh_comp else "Não"
            print(
                f"    {i}. {nome}\n"
                f"       Tipo='{tipo_rel}'{extra_desc}{extra_cod}\n"
                f"       É Adv. Compartilhado? {marca_comp} | Listas: [{listas_tag}]"
            )

    print("\n  [ADVOGADOS COMPARTILHADOS EXTRAÍDOS PARA A REGRA 1.0]")
    if not advogados_compartilhados:
        print("    Nenhum advogado compartilhado encontrado no NPJ 000.")
    else:
        for adv_c in advogados_compartilhados:
            adest_match = distribuidor_base.buscar_na_adest(adv_c)
            adrec_match = distribuidor_base.buscar_na_adrec_todos(adv_c)
            status_10 = (
                f"ESTÁ NA ADEST ('{adest_match}') -> REGRA 1.0 ATIVA!"
                if adest_match
                else (
                    f"NÃO está na ADEST (está em TODOS_ADREC: '{adrec_match}') -> Regra 1.0 NÃO pega"
                    if adrec_match
                    else "FORA DAS LISTAS (não está na ADEST) -> Regra 1.0 NÃO pega"
                )
            )
            print(f"    - {adv_c} -> {status_10}")

    # 2) Lista variações da 8553 encontradas (para entender fallback caso Regra 1.0 não aplique)
    print(f"\n  [VARIAÇÕES DA DEPENDÊNCIA {DEPENDENCIA_ALVO} ENCONTRADAS: {len(variacoes_8553)}]")
    for v in sorted(
        variacoes_8553,
        key=lambda p: int(p.get("numeroOrdemVariacaoSaida", 0)),
        reverse=True,
    ):
        num_v = str(v.get("numeroOrdemVariacaoSaida", "")).zfill(3)
        ativo_v = v.get("indicadorProcessoAtivo", "?")
        estado_v = v.get("textoEstadoProcesso", "?")
        adv_v = str(v.get("nomeAdvogadoProcesso") or "").strip()
        listas_v = classificar_advogado_nas_listas(distribuidor_base, adv_v)
        print(
            f"    - Variação -{num_v} | Ativo: {ativo_v} ({estado_v}) | "
            f"Advogado: '{adv_v or 'Sem Advogado'}' [{listas_v}]"
        )

    # 3) Simulação: NOVA DISTRIBUIÇÃO
    dist_nova = copy.deepcopy(distribuidor_base)
    adv_nova, motivo_nova = dist_nova.definir_advogado_nova_variacao(
        advogados_compartilhados,
        variacoes_8553,
        npj_principal=npj_principal,
    )
    print("\n  [O QUE FARIA EM CASO DE NOVA DISTRIBUIÇÃO]")
    print(f"    -> Advogado escolhido: {adv_nova}")
    print(f"    -> Regra aplicada    : {motivo_nova}")

    # 4) Simulação: REATIVAÇÃO
    print("\n  [O QUE FARIA EM CASO DE REATIVAÇÃO]")

    # 4a) Caso de teste principal da Regra 1.0 na reativação: advogado da variação reativada fora das listas
    dist_reat_fora = copy.deepcopy(distribuidor_base)
    adv_reat_fora, manteve_fora, motivo_reat_fora = dist_reat_fora.definir_advogado_reativacao(
        advogado_npj_baixado="",
        advogados_compartilhados_principal=advogados_compartilhados,
        variacoes_8553=variacoes_8553,
        npj_principal=npj_principal,
        variacao_reativada="???",
    )
    print("    A) Se a variação reativada tiver advogado FORA da ADEST/TODOS ADREC (ou vazio):")
    print(f"       -> Advogado escolhido: {adv_reat_fora} (manteve_advogado={manteve_fora})")
    print(f"       -> Regra aplicada    : {motivo_reat_fora}")

    # 4b) Caso exista(m) variação(ões) da 8553 no NPJ, mostra o que aconteceria ao reativar cada uma
    if variacoes_8553:
        print("    B) Simulação usando o advogado atual de cada variação 8553 existente:")
        for v in sorted(
            variacoes_8553,
            key=lambda p: int(p.get("numeroOrdemVariacaoSaida", 0)),
            reverse=True,
        ):
            num_v = str(v.get("numeroOrdemVariacaoSaida", "")).zfill(3)
            ativo_v = v.get("indicadorProcessoAtivo", "?")
            adv_v = str(v.get("nomeAdvogadoProcesso") or "").strip()

            # Em libNpj.py, só busca compartilhados se adv_v não estiver em ADEST nem TODOS_ADREC
            comp_para_v = (
                advogados_compartilhados
                if (
                    distribuidor_base.buscar_na_adest(adv_v) is None
                    and distribuidor_base.buscar_na_adrec_todos(adv_v) is None
                )
                else []
            )
            dist_v = copy.deepcopy(distribuidor_base)
            adv_res, manteve_v, motivo_v = dist_v.definir_advogado_reativacao(
                advogado_npj_baixado=adv_v,
                advogados_compartilhados_principal=comp_para_v,
                variacoes_8553=variacoes_8553,
                npj_principal=npj_principal,
                variacao_reativada=num_v,
            )
            print(
                f"       - Variação -{num_v} (Ativo={ativo_v}, Adv. atual='{adv_v or 'Vazio'}'):\n"
                f"         -> Advogado: {adv_res} (manteve={manteve_v})\n"
                f"         -> Regra   : {motivo_v}"
            )
    else:
        # Caso não haja variações 8553, mostra também com o advogado do próprio NPJ 000 como referência
        if adv_000:
            dist_000 = copy.deepcopy(distribuidor_base)
            adv_res, manteve_000, motivo_000 = dist_000.definir_advogado_reativacao(
                advogado_npj_baixado=adv_000,
                advogados_compartilhados_principal=advogados_compartilhados,
                variacoes_8553=variacoes_8553,
                npj_principal=npj_principal,
                variacao_reativada="000",
            )
            print(f"    B) Se o advogado da variação reativada fosse o do NPJ 000 ('{adv_000}'):")
            print(f"       -> Advogado escolhido: {adv_res} (manteve={manteve_000})")
            print(f"       -> Regra aplicada    : {motivo_000}")


def ler_npjs_terminal() -> List[str]:
    """Lê uma lista de NPJs 000 digitados ou colados no terminal."""
    print("\nDigite ou cole os NPJs 000 (um por linha, ou separados por vírgula/espaço).")
    print("Quando terminar, pressione ENTER em uma linha vazia (ou digite 'sair' para encerrar):")
    linhas: List[str] = []
    while True:
        try:
            linha = input("> ").strip()
        except EOFError:
            break
        if not linha:
            break
        if linha.lower() in {"sair", "exit", "q"}:
            return []
        linhas.append(linha)

    texto = "\n".join(linhas)
    return [item.strip() for item in re.split(r"[\s,;]+", texto) if item.strip()]


def obter_npjs_iniciais() -> List[str]:
    """Obtém NPJs dos argumentos CLI, de `NPJS_TESTE` ou do terminal."""
    args = [a.strip() for a in sys.argv[1:] if a.strip()]
    if args:
        if len(args) == 1 and os.path.isfile(args[0]):
            with open(args[0], "r", encoding="utf-8") as f:
                return [
                    item.strip()
                    for item in re.split(r"[\s,;]+", f.read())
                    if item.strip()
                ]
        return args

    if NPJS_TESTE:
        return [str(x).strip() for x in NPJS_TESTE if str(x).strip()]

    return ler_npjs_terminal()


def main() -> None:
    listas = carregar_listas_config()
    distribuidor_base = DistribuidorAdvogados(
        advogados_adrec_6=listas["advogados_adrec_6"],
        advogados_adest=listas["advogados_adest"],
        advogados_adrec_todos=listas["advogados_adrec_todos"],
        embaralhar=False,  # Ordem determinística para facilitar os testes
    )

    print("\n[LISTA ADEST CONFIGURADA (Regra 1.0)]")
    for adv in distribuidor_base.advogados_adest:
        print(f"  - {adv}")

    print("\n[FILA 6 ADREC (ordem fixa para teste)]")
    for idx, adv in enumerate(distribuidor_base.advogados, start=1):
        print(f"  {idx}. {adv}")

    print("\nAbrindo navegador para login manual no Portal Jurídico...")
    driver, _ = iniciar_navegador()
    try:
        print("Aguardando login manual...")
        login_manual(driver)
        ensure_base_url(driver)
        print("Login confirmado!")

        npjs = obter_npjs_iniciais()
        while npjs:
            for npj in npjs:
                try:
                    analisar_npj(driver, npj, distribuidor_base)
                except Exception as e:
                    print(f"\n[ERRO] Falha ao analisar '{npj}': {e}")

            print("\n" + "=" * 90)
            resp = input("\nDeseja testar outra lista de NPJs? (s/N): ").strip().lower()
            if resp in {"s", "sim", "y", "yes"}:
                npjs = ler_npjs_terminal()
            else:
                break
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
