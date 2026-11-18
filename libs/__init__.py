# -*- coding: utf-8 -*-
"""
Pacote de bibliotecas internas da automação de publicações do STJ (DIGOR).

Estrutura dos módulos:
    - config: Carregamento e validação do arquivo `stj_config.json`.
    - cache: Criação e controle dos diretórios/arquivos de cache e relatórios.
    - erros: Exceções de negócio (`ErroEsperado`) para desvio ao fluxo manual.
    - helper: Funções utilitárias de portal, formatação de NPJ, partes e log GUI.
    - publicacao: Coleta de publicações pendentes, indicação/descarte e métricas BBTS.
    - dadosStj: Consulta à API do PJe/CNJ e extração de partes, polo e tramitação.
    - bb_n_parte: Regras de descarte automático para publicações "BB Não Parte".
    - conferiNumeroUnico: Resolução do NPJ principal a partir do Número Único CNJ.
    - distribuidor: Fila circular (round-robin) de advogados para novas distribuições.
    - libNpj: Conferência, reativação, atualização (CNJ/Outros) e cadastro de NPJ.
    - relatorio: Geração de relatórios Excel locais/OneDrive, atualização Power BI e limpeza.
"""
