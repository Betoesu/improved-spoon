# -*- coding: utf-8 -*-
"""
Simulação (shadow execution) de distribuição de advogados (ADEST / ADREC)
para novas variações, variações reativadas e geração de relatórios em TXT e XLSX.

Este módulo reproduz exatamente a lógica da `versao_lista_advogados` em segundo
plano (sem interferir nas regras de negócio da `versao_organizada`), registrando
cada passo percorrido na árvore de decisão para gerar:
    1) Relatório TXT de contagem e detalhamento por advogado.
    2) Relatório XLSX passo a passo (com células vazias nos passos não percorridos
       por cada publicação) e resumo nas últimas colunas com o total atribuído
       a cada advogado (Reativadas e Novas Variações).
"""

from datetime import datetime
import random
from typing import Any, Dict, List, Optional, Set, Tuple
import unicodedata

import pandas as pd

PREPOSICOES_IGNORADAS: Set[str] = {"DA", "DE", "DO", "DAS", "DOS", "E"}

COLUNAS_PASSOS_XLSX: List[str] = [
    "Variação Já Cadastrada e Ativa?",
    "É Reativação?",
    "Reativação: Advogado Atual é da ADEST ou TODOS ADREC?",
    "Advogado Compartilhado e é da ADEST?",
    "Tem Variação da 8553?",
    "Tem Somente 1 Variação da 8553?",
    "1 Variação 8553: Advogado é da ADEST?",
    "1 Variação 8553: Advogado é de TODOS ADREC?",
    "Mais de 1 Variação 8553 (Maior p/ Menor): Advogado é da ADEST?",
    "Mais de 1 Variação 8553 (Maior p/ Menor): Advogado é de TODOS ADREC?",
    "Fila dos 6 Advogados da ADREC?",
    "Reativação: Pulou Próximo da Fila 6 ADREC (Evitar 2x Seguidas)?",
]


def criar_passos_vazios() -> Dict[str, str]:
    """Retorna um dicionário com todas as colunas de passos inicializadas vazias ('')."""
    return {col: "" for col in COLUNAS_PASSOS_XLSX}


def _normalizar_tokens_nome(nome: str) -> List[str]:
    """
    Normaliza um nome (sem acentos, maiúsculo, sem preposições e unificando grafias como
    'CK' -> 'K', 'JR' -> 'JUNIOR', 'LUIZ' -> 'LUIS') para comparação segura entre
    nomes do Portal Jurídico e as listas configuradas.
    """
    sem_acentos = (
        unicodedata.normalize("NFKD", nome)
        .encode("ASCII", "ignore")
        .decode("ASCII")
        .upper()
    )
    tokens = []
    for token in sem_acentos.split():
        limpo = "".join(ch for ch in token if ch.isalpha())
        if not limpo or limpo in PREPOSICOES_IGNORADAS:
            continue
        limpo = limpo.replace("CK", "K")
        if limpo == "JR":
            limpo = "JUNIOR"
        elif limpo == "LUIZ":
            limpo = "LUIS"
        tokens.append(limpo)
    return tokens


def nomes_advogados_correspondem(nome_a: Optional[str], nome_b: Optional[str]) -> bool:
    """
    Verifica se dois nomes de advogado referem-se à mesma pessoa.
    Aceita tanto correspondência exata quanto nome parcial (todos os tokens do nome menor
    presentes no nome completo).
    """
    if not nome_a or not nome_b:
        return False

    tokens_a = _normalizar_tokens_nome(nome_a)
    tokens_b = _normalizar_tokens_nome(nome_b)
    if not tokens_a or not tokens_b:
        return False

    menor, maior = (
        (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    )
    return all(t in maior for t in menor)


class DistribuidorAdvogados:
    """
    Gerencia as listas de advogados (ADEST, 6 da ADREC e TODOS da ADREC), a fila
    circular embaralhada dos 6 advogados da ADREC e a contabilização de publicações
    distribuídas para cada advogado (em modo de simulação/shadow ou ativo).
    """

    def __init__(
        self,
        advogados_adrec_6: List[str],
        advogados_adest: Optional[List[str]] = None,
        advogados_adrec_todos: Optional[List[str]] = None,
        embaralhar: bool = True,
    ) -> None:
        if not advogados_adrec_6:
            raise ValueError(
                "A lista dos 6 advogados da ADREC está vazia. Verifique o stj_config.json."
            )

        self.advogados_adest: List[str] = list(advogados_adest or [])
        self.advogados_adrec_6_original: List[str] = list(advogados_adrec_6)
        self.advogados_adrec_todos: List[str] = list(
            advogados_adrec_todos or advogados_adrec_6
        )

        # Fila circular dos 6 advogados da ADREC (embaralhada a cada execução)
        self.advogados: List[str] = list(advogados_adrec_6)
        if embaralhar:
            random.shuffle(self.advogados)

        self.indice_atual: int = 0
        self.total_distribuidos: int = 0
        self.registros_distribuicao: List[Dict[str, Any]] = []

    def proximo_advogado(self) -> str:
        """
        Retorna o próximo advogado da fila dos 6 advogados da ADREC e avança a posição.
        Quando chega ao fim da lista, volta automaticamente ao índice 0.
        """
        advogado_escolhido = self.advogados[self.indice_atual]
        self.indice_atual = (self.indice_atual + 1) % len(self.advogados)
        self.total_distribuidos += 1
        return advogado_escolhido

    def _buscar_em_lista(
        self, nome_consulta: Optional[str], lista_alvo: List[str]
    ) -> Optional[str]:
        """
        Procura `nome_consulta` dentro de `lista_alvo`.
        Se encontrar, retorna o nome oficial padronizado da lista (ou o mais completo).
        """
        if not nome_consulta:
            return None
        nome_limpo = nome_consulta.strip()
        if not nome_limpo:
            return None

        for item_lista in lista_alvo:
            if nomes_advogados_correspondem(nome_limpo, item_lista):
                return item_lista.strip()
        return None

    def buscar_na_adest(self, nome_advogado: Optional[str]) -> Optional[str]:
        """Verifica se o advogado está na lista da ADEST."""
        return self._buscar_em_lista(nome_advogado, self.advogados_adest)

    def buscar_na_adrec_6(self, nome_advogado: Optional[str]) -> Optional[str]:
        """Verifica se o advogado está na lista dos 6 advogados da ADREC."""
        return self._buscar_em_lista(nome_advogado, self.advogados_adrec_6_original)

    def buscar_na_adrec_todos(self, nome_advogado: Optional[str]) -> Optional[str]:
        """Verifica se o advogado está na lista de TODOS os advogados da ADREC."""
        return self._buscar_em_lista(nome_advogado, self.advogados_adrec_todos)

    def _aplicar_arvore_compartilhado_e_8553(
        self,
        advogados_compartilhados_principal: List[str],
        variacoes_8553: List[Dict[str, Any]],
        passos: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, str, bool, Dict[str, str]]:
        """
        Executa a árvore de decisão compartilhada entre Novas Variações e Reativações (ramo 'Se não'):
            0) Condução compartilhada no NPJ 000 com advogado na ADEST.
            1.1) Única variação da 8553 (ADEST -> TODOS ADREC -> Fila 6 ADREC).
            1.2) Múltiplas variações da 8553: analisa da MAIOR para a MENOR (ADEST -> TODOS ADREC -> Fila 6 ADREC).
            1.3) Sem variação da 8553 (-> Fila 6 ADREC).

        Preenche `passos` apenas nas colunas efetivamente avaliadas para este processo.
        """
        if passos is None:
            passos = criar_passos_vazios()

        # 0) Verifica condução compartilhada no NPJ principal (variação 000)
        for adv_comp in advogados_compartilhados_principal:
            adv_adest = self.buscar_na_adest(adv_comp)
            if adv_adest:
                passos["Advogado Compartilhado e é da ADEST?"] = "Sim"
                return (
                    adv_adest,
                    f"Condução compartilhada no NPJ 000 (ADEST): {adv_adest}",
                    False,
                    passos,
                )

        passos["Advogado Compartilhado e é da ADEST?"] = "Não"

        # 1) Verifica se existem variações da dependência 8553
        if len(variacoes_8553) == 1:
            passos["Tem Variação da 8553?"] = "Sim"
            passos["Tem Somente 1 Variação da 8553?"] = "Sim"

            # 1.1 Somente uma variação da 8553
            unica_var = variacoes_8553[0]
            num_var = str(unica_var.get("numeroOrdemVariacaoSaida", "")).zfill(3)
            adv_var = unica_var.get("nomeAdvogadoProcesso", "")

            adv_adest = self.buscar_na_adest(adv_var)
            if adv_adest:
                passos["1 Variação 8553: Advogado é da ADEST?"] = "Sim"
                return (
                    adv_adest,
                    f"Regra 1.1 (Única variação 8553 -{num_var} | Advogado na ADEST): {adv_adest}",
                    False,
                    passos,
                )

            passos["1 Variação 8553: Advogado é da ADEST?"] = "Não"

            adv_adrec_todos = self.buscar_na_adrec_todos(adv_var)
            if adv_adrec_todos:
                passos["1 Variação 8553: Advogado é de TODOS ADREC?"] = "Sim"
                return (
                    adv_adrec_todos,
                    f"Regra 1.1 (Única variação 8553 -{num_var} | Advogado em TODOS ADREC): {adv_adrec_todos}",
                    False,
                    passos,
                )

            passos["1 Variação 8553: Advogado é de TODOS ADREC?"] = "Não"
            passos["Fila dos 6 Advogados da ADREC?"] = "Sim"
            proximo = self.proximo_advogado()
            return (
                proximo,
                f"Regra 1.1 (Única variação 8553 -{num_var} com '{adv_var}' fora das listas -> Fila 6 ADREC): {proximo}",
                True,
                passos,
            )

        elif len(variacoes_8553) > 1:
            passos["Tem Variação da 8553?"] = "Sim"
            passos["Tem Somente 1 Variação da 8553?"] = "Não"

            # 1.2 Mais de uma variação da 8553 -> verifica da MAIOR para a MENOR variação
            variacoes_ordenadas = sorted(
                variacoes_8553,
                key=lambda p: int(p.get("numeroOrdemVariacaoSaida", 0)),
                reverse=True,
            )
            resumo_verificadas: List[str] = []

            for var_atual in variacoes_ordenadas:
                num_var = str(var_atual.get("numeroOrdemVariacaoSaida", "")).zfill(3)
                adv_var = var_atual.get("nomeAdvogadoProcesso", "")

                adv_adest = self.buscar_na_adest(adv_var)
                if adv_adest:
                    passos["Mais de 1 Variação 8553 (Maior p/ Menor): Advogado é da ADEST?"] = "Sim"
                    return (
                        adv_adest,
                        f"Regra 1.2 (Variação 8553 -{num_var} [da maior p/ menor] | Advogado na ADEST): {adv_adest}",
                        False,
                        passos,
                    )

                adv_adrec_todos = self.buscar_na_adrec_todos(adv_var)
                if adv_adrec_todos:
                    passos["Mais de 1 Variação 8553 (Maior p/ Menor): Advogado é da ADEST?"] = "Não"
                    passos["Mais de 1 Variação 8553 (Maior p/ Menor): Advogado é de TODOS ADREC?"] = "Sim"
                    return (
                        adv_adrec_todos,
                        f"Regra 1.2 (Variação 8553 -{num_var} [da maior p/ menor] | Advogado em TODOS ADREC): {adv_adrec_todos}",
                        False,
                        passos,
                    )

                resumo_verificadas.append(f"-{num_var} ('{adv_var}')")

            # Acabaram todas as variações da 8553 (da maior para a menor) e nenhuma tinha advogado nas listas
            passos["Mais de 1 Variação 8553 (Maior p/ Menor): Advogado é da ADEST?"] = "Não"
            passos["Mais de 1 Variação 8553 (Maior p/ Menor): Advogado é de TODOS ADREC?"] = "Não"
            passos["Fila dos 6 Advogados da ADREC?"] = "Sim"

            proximo = self.proximo_advogado()
            detalhe_vars = ", ".join(resumo_verificadas)
            return (
                proximo,
                f"Regra 1.2 (Variações 8553 [{detalhe_vars}] fora das listas -> Fila 6 ADREC): {proximo}",
                True,
                passos,
            )

        # 1.3 Não possui variação da 8553 -> distribui para o próximo da fila dos 6 da ADREC
        passos["Tem Variação da 8553?"] = "Não"
        passos["Fila dos 6 Advogados da ADREC?"] = "Sim"
        proximo = self.proximo_advogado()
        return (
            proximo,
            f"Regra 1.3 (Sem variação prévia da 8553 -> Fila 6 ADREC): {proximo}",
            True,
            passos,
        )

    def definir_advogado_nova_variacao(
        self,
        advogados_compartilhados_principal: List[str],
        variacoes_8553: List[Dict[str, Any]],
    ) -> Tuple[str, str, Dict[str, str]]:
        """
        Aplica a árvore de decisão para DISTRIBUIÇÃO DE NOVAS VARIAÇÕES.

        Returns:
            Tuple[str, str, Dict[str, str]]: `(advogado_escolhido, descricao_regra_aplicada, passos)`
        """
        passos = criar_passos_vazios()
        passos["Variação Já Cadastrada e Ativa?"] = "Não"
        passos["É Reativação?"] = "Não"

        advogado, motivo, _, passos = self._aplicar_arvore_compartilhado_e_8553(
            advogados_compartilhados_principal,
            variacoes_8553,
            passos=passos,
        )
        return advogado, motivo, passos

    def definir_advogado_reativacao(
        self,
        advogado_npj_baixado: Optional[str],
        advogados_compartilhados_principal: Optional[List[str]] = None,
        variacoes_8553: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[str, bool, str, Dict[str, str]]:
        """
        Aplica a regra de redistribuição de VARIAÇÕES REATIVADAS:
        1. Verifica se o advogado da variação reativada está na lista da ADEST ou
           em TODOS os advogados da ADREC:
           - Se SIM: mantém o advogado atual (`manteve_advogado = True`).
           - Se NÃO: aplica a árvore (Condução Compartilhada no NPJ 000 -> Variações da 8553
             única / maior número -> Fila dos 6 advogados da ADREC).
        2. Regra anti-"duas vezes seguidas":
           - Se a publicação reativada foi distribuída para um advogado específico dos
             6 da ADREC E o próximo da lista (`self.advogados[self.indice_atual]`) é ele,
             pula para o próximo nome da fila.

        Returns:
            Tuple[str, bool, str, Dict[str, str]]:
                `(advogado_escolhido, manteve_advogado_original, descricao_regra, passos)`
        """
        advogados_compartilhados_principal = advogados_compartilhados_principal or []
        variacoes_8553 = variacoes_8553 or []

        passos = criar_passos_vazios()
        passos["Variação Já Cadastrada e Ativa?"] = "Não"
        passos["É Reativação?"] = "Sim"

        adv_na_adest = self.buscar_na_adest(advogado_npj_baixado)
        adv_em_todos_adrec = self.buscar_na_adrec_todos(advogado_npj_baixado)
        adv_atual_valido = adv_na_adest or adv_em_todos_adrec

        if adv_atual_valido is not None:
            passos["Reativação: Advogado Atual é da ADEST ou TODOS ADREC?"] = "Sim"
            advogado_escolhido = adv_atual_valido
            manteve_advogado = True
            origem_lista = "ADEST" if adv_na_adest else "TODOS ADREC"
            motivo = (
                f"Reativação (Advogado atual '{advogado_escolhido}' está em {origem_lista} -> Mantido)"
            )
        else:
            passos["Reativação: Advogado Atual é da ADEST ou TODOS ADREC?"] = "Não"
            advogado_escolhido, motivo_arvore, _, passos = self._aplicar_arvore_compartilhado_e_8553(
                advogados_compartilhados_principal,
                variacoes_8553,
                passos=passos,
            )
            manteve_advogado = False
            motivo = (
                f"Reativação (Advogado anterior '{advogado_npj_baixado}' fora das listas -> {motivo_arvore})"
            )

        # Regra especial: Se a publicação reativada foi distribuída para um advogado
        # específico dos 6 da ADREC E o próximo da fila é ele mesmo, pula para o próximo
        # nome para não cair nele "duas vezes seguidas".
        if self.buscar_na_adrec_6(advogado_escolhido) is not None:
            proximo_atual_fila = self.proximo_da_fila()
            if nomes_advogados_correspondem(advogado_escolhido, proximo_atual_fila):
                self.indice_atual = (self.indice_atual + 1) % len(self.advogados)
                novo_proximo = self.proximo_da_fila()
                passos["Reativação: Pulou Próximo da Fila 6 ADREC (Evitar 2x Seguidas)?"] = "Sim"
                motivo += (
                    f" | [Fila pulou '{proximo_atual_fila}' para evitar 2x seguidas -> próximo da fila: '{novo_proximo}']"
                )
            else:
                passos["Reativação: Pulou Próximo da Fila 6 ADREC (Evitar 2x Seguidas)?"] = "Não"

        return advogado_escolhido, manteve_advogado, motivo, passos

    def proximo_da_fila(self) -> str:
        """Apenas consulta quem é o próximo advogado da fila dos 6 da ADREC sem avançar."""
        return self.advogados[self.indice_atual]

    def registrar_publicacao_advogado(
        self,
        advogado: str,
        tipo: str,
        publicacao: str,
        npj: str,
        regra: str = "",
        id_publicacao: str = "",
        passos: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Registra uma publicação atribuída a um advogado para os relatórios TXT e XLSX finais.

        Args:
            advogado: Nome do advogado responsável (na simulação da lista de advogados).
            tipo: 'Nova Variação', 'Reativada' ou 'Já Cadastrada'.
            publicacao: Número CNJ da publicação.
            npj: Identificador/máscara do NPJ.
            regra: Descrição da regra aplicada.
            id_publicacao: Identificador único da publicação no portal.
            passos: Dicionário com as colunas passo a passo preenchidas para esta publicação.
        """
        nome_padronizado = (
            self.buscar_na_adest(advogado)
            or self.buscar_na_adrec_todos(advogado)
            or (advogado or "NÃO IDENTIFICADO").strip()
        )
        if passos is None:
            passos = criar_passos_vazios()
            if tipo == "Já Cadastrada":
                passos["Variação Já Cadastrada e Ativa?"] = "Sim"

        self.registros_distribuicao.append(
            {
                "id_publicacao": str(id_publicacao),
                "advogado": nome_padronizado,
                "tipo": tipo,
                "publicacao": str(publicacao),
                "npj": str(npj),
                "regra": regra,
                "horario": datetime.now().strftime("%H:%M:%S"),
                "passos": passos,
            }
        )

    def _contar_por_advogado(self, nome_adv: str) -> Tuple[int, int, int, int]:
        """Retorna `(novas, reativadas, ja_cadastradas, total)` para `nome_adv`."""
        novas = sum(
            1
            for r in self.registros_distribuicao
            if nomes_advogados_correspondem(r["advogado"], nome_adv)
            and r["tipo"] == "Nova Variação"
        )
        reativ = sum(
            1
            for r in self.registros_distribuicao
            if nomes_advogados_correspondem(r["advogado"], nome_adv)
            and r["tipo"] == "Reativada"
        )
        ja_cad = sum(
            1
            for r in self.registros_distribuicao
            if nomes_advogados_correspondem(r["advogado"], nome_adv)
            and r["tipo"] == "Já Cadastrada"
        )
        return novas, reativ, ja_cad, (novas + reativ + ja_cad)

    def formatar_relatorio_txt(self) -> str:
        """
        Gera o conteúdo organizado em TXT com a quantidade de publicações que foram
        para cada advogado (separado por grupo e por tipo de operação) e o detalhamento.
        """
        agora_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        total_geral = len(self.registros_distribuicao)
        total_novas = sum(
            1 for r in self.registros_distribuicao if r["tipo"] == "Nova Variação"
        )
        total_reativadas = sum(
            1 for r in self.registros_distribuicao if r["tipo"] == "Reativada"
        )
        total_ja_cadastradas = sum(
            1 for r in self.registros_distribuicao if r["tipo"] == "Já Cadastrada"
        )

        linhas: List[str] = []
        linhas.append("=" * 88)
        linhas.append(
            "          RELATÓRIO DE DISTRIBUIÇÃO DE PUBLICAÇÕES POR ADVOGADO (STJ)"
        )
        linhas.append("=" * 88)
        linhas.append(f"Data/Hora de Geração : {agora_str}")
        linhas.append(f"Total Distribuído    : {total_geral} publicação(ões)")
        linhas.append(f"  - Novas Variações        : {total_novas}")
        linhas.append(f"  - Variações Reativadas   : {total_reativadas}")
        linhas.append(f"  - Já Cadastradas (Ativas): {total_ja_cadastradas}")
        linhas.append("")
        linhas.append("Ordem Sorteada da Fila (6 Advogados da ADREC) nesta execução:")
        for idx, adv in enumerate(self.advogados, start=1):
            linhas.append(f"  {idx}º {adv}")
        linhas.append("")
        linhas.append("-" * 88)
        linhas.append("1. QUANTIDADE DE PUBLICAÇÕES POR ADVOGADO")
        linhas.append("-" * 88)

        cabecalho_tabela = (
            f"{'Advogado':<44} | {'Novas':>7} | {'Reativ.':>7} | {'Já Cad.':>7} | {'TOTAL':>7}"
        )
        separador_tabela = "-" * 45 + "+" + "-" * 9 + "+" + "-" * 9 + "+" + "-" * 9 + "+" + "-" * 8

        # Grupo 1: 6 Advogados da ADREC
        linhas.append("\n[FILA PRINCIPAL - 6 ADVOGADOS DA ADREC]")
        linhas.append(cabecalho_tabela)
        linhas.append(separador_tabela)
        for adv in self.advogados_adrec_6_original:
            novas, reativ, ja_cad, tot = self._contar_por_advogado(adv)
            linhas.append(
                f"{adv:<44} | {novas:>7} | {reativ:>7} | {ja_cad:>7} | {tot:>7}"
            )

        # Grupo 2: Demais Advogados de TODOS da ADREC (ex: Rodney Rossi)
        demais_adrec = [
            adv
            for adv in self.advogados_adrec_todos
            if self.buscar_na_adrec_6(adv) is None
        ]
        if demais_adrec:
            linhas.append("\n[DEMAIS ADVOGADOS - TODOS ADREC]")
            linhas.append(cabecalho_tabela)
            linhas.append(separador_tabela)
            for adv in demais_adrec:
                novas, reativ, ja_cad, tot = self._contar_por_advogado(adv)
                linhas.append(
                    f"{adv:<44} | {novas:>7} | {reativ:>7} | {ja_cad:>7} | {tot:>7}"
                )

        # Grupo 3: Advogados da ADEST
        linhas.append("\n[ADVOGADOS DA ADEST]")
        linhas.append(cabecalho_tabela)
        linhas.append(separador_tabela)
        for adv in self.advogados_adest:
            novas, reativ, ja_cad, tot = self._contar_por_advogado(adv)
            linhas.append(
                f"{adv:<44} | {novas:>7} | {reativ:>7} | {ja_cad:>7} | {tot:>7}"
            )

        # Grupo 4: Outros advogados (caso algum NPJ já cadastrado ativo estivesse com terceiro)
        outros_advs: List[str] = []
        for reg in self.registros_distribuicao:
            nome_reg = reg["advogado"]
            if (
                self.buscar_na_adest(nome_reg) is None
                and self.buscar_na_adrec_todos(nome_reg) is None
                and not any(nomes_advogados_correspondem(nome_reg, o) for o in outros_advs)
            ):
                outros_advs.append(nome_reg)

        if outros_advs:
            linhas.append("\n[OUTROS ADVOGADOS (NPJs JÁ CADASTRADOS ATIVOS)]")
            linhas.append(cabecalho_tabela)
            linhas.append(separador_tabela)
            for adv in outros_advs:
                novas, reativ, ja_cad, tot = self._contar_por_advogado(adv)
                linhas.append(
                    f"{adv:<44} | {novas:>7} | {reativ:>7} | {ja_cad:>7} | {tot:>7}"
                )

        linhas.append("")
        linhas.append("-" * 88)
        linhas.append("2. DETALHAMENTO DAS PUBLICAÇÕES DISTRIBUÍDAS")
        linhas.append("-" * 88)
        if not self.registros_distribuicao:
            linhas.append("Nenhuma publicação distribuída nesta execução.")
        else:
            for idx, reg in enumerate(self.registros_distribuicao, start=1):
                linhas.append(
                    f"{idx:03d}. [{reg['horario']}] Advogado  : {reg['advogado']}\n"
                    f"     Tipo      : {reg['tipo']}\n"
                    f"     Publicação: {reg['publicacao']} | NPJ: {reg['npj']}\n"
                    f"     Regra     : {reg['regra']}"
                )
                linhas.append("")

        linhas.append("=" * 88)
        return "\n".join(linhas)

    def gerar_dataframe_xlsx(self) -> pd.DataFrame:
        """
        Gera o DataFrame para exportação em `.xlsx` onde:
            - As primeiras colunas trazem `ID Publicação`, `CNJ Publicação`, `NPJ` e `Tipo`.
            - Cada coluna seguinte representa um passo da árvore de decisão (`Sim` / `Não`),
              mantendo células vazias (`""`) para passos pelos quais o processo não passou.
            - A coluna seguinte indica para qual advogado foi o processo (`Para Qual Advogado Foi o Processo`).
            - As últimas colunas trazem o nome de cada advogado (ADEST e ADREC) e o número
              de publicações atribuídas a eles das reativadas e das novas variações.
        """
        # Lista consolidada de advogados (ADEST + TODOS ADREC, sem duplicatas)
        todos_advogados_listas: List[str] = []
        for adv in self.advogados_adest + self.advogados_adrec_todos:
            if not any(nomes_advogados_correspondem(adv, existente) for existente in todos_advogados_listas):
                todos_advogados_listas.append(adv)

        # Calcula contagens de Novas Variações e Reativadas por advogado
        resumo_contagem_adv: Dict[str, str] = {}
        for adv in todos_advogados_listas:
            novas, reativ, _, _ = self._contar_por_advogado(adv)
            total_atribuidas = novas + reativ
            resumo_contagem_adv[adv] = (
                f"{total_atribuidas} (Novas: {novas} | Reativadas: {reativ})"
            )

        linhas_planilha: List[Dict[str, Any]] = []

        if not self.registros_distribuicao:
            linha_vazia: Dict[str, Any] = {
                "ID Publicação": "",
                "CNJ Publicação": "",
                "NPJ": "",
                "Tipo": "",
            }
            for col_passo in COLUNAS_PASSOS_XLSX:
                linha_vazia[col_passo] = ""
            linha_vazia["Para Qual Advogado Foi o Processo"] = ""
            for adv in todos_advogados_listas:
                linha_vazia[adv] = resumo_contagem_adv[adv]
            linhas_planilha.append(linha_vazia)
        else:
            for idx, reg in enumerate(self.registros_distribuicao):
                passos_reg = reg.get("passos") or criar_passos_vazios()
                linha: Dict[str, Any] = {
                    "ID Publicação": reg.get("id_publicacao", ""),
                    "CNJ Publicação": reg.get("publicacao", ""),
                    "NPJ": reg.get("npj", ""),
                    "Tipo": reg.get("tipo", ""),
                }
                for col_passo in COLUNAS_PASSOS_XLSX:
                    linha[col_passo] = passos_reg.get(col_passo, "")

                linha["Para Qual Advogado Foi o Processo"] = reg.get("advogado", "")

                # Nas últimas colunas: nome de cada advogado e o número de publicações
                # atribuídas a eles das reativadas e das novas variações (exibido na 1ª linha)
                for adv in todos_advogados_listas:
                    linha[adv] = resumo_contagem_adv[adv] if idx == 0 else ""

                linhas_planilha.append(linha)

        return pd.DataFrame(linhas_planilha)
