# -*- coding: utf-8 -*-
"""
Definição de exceções customizadas de regra de negócio da automação STJ.
"""


class ErroEsperado(Exception):
    """Exceção para marcar erros esperados no sistema (encaminhados para fila Manual)."""
    pass
