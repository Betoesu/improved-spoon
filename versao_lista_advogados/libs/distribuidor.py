# -*- coding: utf-8 -*-
"""
Controle de distribuição sequencial (fila circular / round-robin) de advogados.

Este módulo garante que as novas distribuições de processos sigam exatamente a
ordem definida na lista de advogados do arquivo de configuração (`stj_config.json`).
Quando o último advogado da lista recebe um processo, a próxima distribuição
retorna automaticamente ao primeiro advogado da lista, repetindo o ciclo até que
todas as publicações acabem.
"""

import random
from typing import List, Optional, Tuple


class DistribuidorAdvogados:
    """
    Fila circular de advogados para distribuição de novos processos (NPJs incidentais)
    e verificação de advogados em processos reativados.

    Ao ser inicializada, embaralha aleatoriamente (`random.shuffle`) a lista de
    advogados para que cada execução da automação siga uma ordem inicial diferente,
    evitando que os primeiros da lista no JSON sempre recebam mais processos.
    """

    def __init__(self, advogados: List[str], embaralhar: bool = True) -> None:
        if not advogados:
            raise ValueError(
                "A lista de advogados está vazia. Verifique a chave 'advogados' no stj_config.json."
            )
        # Cria uma cópia da lista e embaralha a ordem a cada execução
        self.advogados: List[str] = list(advogados)
        if embaralhar:
            random.shuffle(self.advogados)

        self.indice_atual: int = 0
        self.total_distribuidos: int = 0

    def proximo_advogado(self) -> str:
        """
        Retorna o advogado da vez na fila e avança o ponteiro para o próximo.

        Deve ser chamado quando ocorrer uma nova distribuição ou quando um NPJ
        reativado possuir um advogado que não faz mais parte da lista.
        """
        advogado_escolhido = self.advogados[self.indice_atual]
        self.indice_atual = (self.indice_atual + 1) % len(self.advogados)
        self.total_distribuidos += 1
        return advogado_escolhido

    def obter_advogado_da_lista(self, nome_advogado: Optional[str]) -> Optional[str]:
        """
        Verifica se `nome_advogado` pertence à lista de advogados configurada.

        Args:
            nome_advogado (Optional[str]): Nome do advogado cadastrado no NPJ baixado.

        Returns:
            Optional[str]: O nome padronizado do advogado na lista caso pertença a ela,
            ou `None` caso não esteja na lista.
        """
        if not nome_advogado:
            return None

        nome_norm = nome_advogado.strip().upper()
        if not nome_norm:
            return None

        for adv in self.advogados:
            adv_norm = adv.strip().upper()
            if nome_norm == adv_norm or nome_norm in adv_norm or adv_norm in nome_norm:
                return adv

        return None

    def esta_na_lista(self, nome_advogado: Optional[str]) -> bool:
        """Retorna `True` se o advogado informado estiver na lista de advogados."""
        return self.obter_advogado_da_lista(nome_advogado) is not None

    def definir_advogado_reativacao(self, advogado_npj_baixado: Optional[str]) -> Tuple[str, bool]:
        """
        Regra para processos que precisaram ser reativados:
        - Se o advogado do NPJ baixado estiver na lista de advogados, mantém-no
          (sem avançar a fila).
        - Se não estiver na lista, aciona a fila (`proximo_advogado`).

        Args:
            advogado_npj_baixado (Optional[str]): Advogado vinculado ao NPJ baixado.

        Returns:
            Tuple[str, bool]: Par `(advogado_escolhido, manteve_advogado_do_baixado)`.
        """
        advogado_na_lista = self.obter_advogado_da_lista(advogado_npj_baixado)
        if advogado_na_lista is not None:
            return advogado_na_lista, True

        return self.proximo_advogado(), False

    def proximo_da_fila(self) -> str:
        """Apenas consulta quem é o próximo advogado sem avançar a fila (útil para debug/logs)."""
        return self.advogados[self.indice_atual]
