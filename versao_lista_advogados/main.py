# -*- coding: utf-8 -*-
"""
Automação de cadastro de processos no STJ.

Este módulo executa, ponta a ponta, a rotina de automação de publicações do
portal jurídico do STJ: realiza login, busca e cacheia publicações, coleta
dados no PJe, confere/edita/cadastra/reativa NPJ, indica a publicação no sistema e, ao final,
gera relatório consolidado. Pensado para rodar com intervenção mínima.

Principais elementos expostos:
    - Funções de orquestração: `run_automation`, `start_automation`, `kill_program`
    - Classe de interface gráfica (Tkinter): `Interface`

Dependências externas (libs internas do projeto):
    - DijurLib (navegador, login, API)
    - libs.* (cache, publicação, PJe, BB-não-parte, NPJ, relatório)

Observações:
    * A automação persiste estado em um cache (Excel + TXT) para permitir
      retomadas, evitando retrabalho e mantendo consistencia ao reexecutar o programa.
    * O processamento principal roda em *thread* separada para manter a GUI
      responsiva.

Autores:
    Desenvolvedor: Felipe Nogueira Casimiro
    Supervisora: Ferdinanda Borges Carneiro
"""

import ast
from datetime import datetime
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import PhotoImage, messagebox, scrolledtext
import traceback
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

# Importações da biblioteca corporativa DijurLib
from DijurLib.api.helpers import get_logged_user
from DijurLib.portal.login import login_manual
from DijurLib.utils.navegador import iniciar_navegador

# Importações dos módulos internos do projeto
from libs.bb_n_parte import conferir_bbnaoparte
from libs.cache import cria_cache
from libs.conferiNumeroUnico import conferir_numero_unico
from libs.config import load_config, obter_lista_advogados
from libs.dadosStj import pegar_dados_cnj
from libs.distribuidor import DistribuidorAdvogados
from libs.erros import ErroEsperado
from libs.helper import ensure_base_url, montar_npj, update_log
from libs.libNpj import conferir_npj
from libs.publicacao import (
    indica_pubicacao,
    pegar_dados_tratadas_ultimos_7_dias,
    pegar_publicacoes,
)
from libs.relatorio import gerar_relatorio

# ------------------------------------------------------------------------------
# Sinalização global para controle da thread da automação
# ------------------------------------------------------------------------------
STOP_EVENT = threading.Event()

# ------------------------------------------------------------------------------
# Constantes e mapeamentos de negócio
# ------------------------------------------------------------------------------
CONFIG = load_config()
LISTA_ADVOGADOS: List[str] = obter_lista_advogados(CONFIG)

# Mapeia a descrição textual do polo para o código utilizado no sistema.
DICT_POLO: Dict[str, str] = {
    "Ativo": "A",
    "Passivo": "P",
    "Neutro": "N",
}

# Tipos de processo que exigem tratamento manual obrigatório
TIPOS_PROCESSO_MANUAIS = [
    "CONFLITO DE COMPETÊNCIA",
    "PETIÇÃO",
    "MANDADO DE SEGURANÇA",
    "Ação Rescisória",
    "AÇÃO RECISÓRIA",
]


# ------------------------------------------------------------------------------
# Utilitários de Controle de Processo e Cache de Publicações
# ------------------------------------------------------------------------------
def kill_program() -> None:
    """
    Finaliza o processo de forma imediata.

    Atenção:
        Usa ``os._exit(0)`` para finalizar o *processo* do Python de maneira
        abrupta (sem acionar `finally`, *atexit*, *flush* de buffers, etc.).
        Deve ser utilizado apenas em cenários de emergência na GUI.

    Returns:
        None: Não há retorno. O processo é encerrado.
    """
    os._exit(0)


def load_processed_publications(publicacoes_txt: str) -> Set[str]:
    """Carrega os IDs de publicações já processadas a partir do arquivo."""
    if not os.path.exists(publicacoes_txt):
        return set()
    with open(publicacoes_txt, "r") as f:
        return set(line.strip() for line in f.readlines())


def save_processed_publication(publicacoes_txt: str, id_publicacao: str) -> None:
    """
    Acrescenta o ID de uma publicação processada ao arquivo de controle.

    O arquivo (TXT) atua como *marcador* para evitar reprocessamentos na
    reexecução do programa.

    Args:
        publicacoes_txt (str): Caminho do arquivo TXT de controle.
        id_publicacao (str): Identificador único da publicação no portal.

    Returns:
        None
    """
    with open(publicacoes_txt, "a") as f:
        f.write(id_publicacao + "\n")


# ------------------------------------------------------------------------------
# Sub-etapas Internas da Orquestração (`run_automation`)
# ------------------------------------------------------------------------------
def _iniciar_sessao_portal(log_widget: tk.Widget) -> Tuple[Any, Any, Optional[str]]:
    """Abre o navegador, solicita o login manual e retorna (driver, wait, matricula)."""
    update_log(log_widget, "Abrindo Navegador...")
    try:
        driver, wait = iniciar_navegador()
    except Exception:
        messagebox.showerror(
            "Erro",
            "Erro ao abrir o navegador!\n"
            "Cheque o arquivo navegador_error_log.txt gerado na pasta do executável para mais instruções.\n",
        )
        kill_program()

    update_log(log_widget, "Fazendo login...")
    login_manual(driver)
    matricula = get_logged_user(driver).get("chave")
    return driver, wait, matricula


def _carregar_ou_buscar_publicacoes(
    driver: Any,
    excel_automacao: str,
    log_widget: tk.Widget,
) -> pd.DataFrame:
    """Carrega a planilha de cache ou busca novas publicações pendentes no portal."""
    df = pd.read_excel(excel_automacao, dtype=str)

    if df.empty:
        update_log(log_widget, "Buscando publicações...")
        df = pegar_publicacoes(driver, df)
        df.to_excel(excel_automacao, index=False)
        update_log(log_widget, f"{len(df)} publicações foram encontradas.")
    else:
        # Caso a data no cache não seja a de hoje, pergunta se o usuário deseja continuar
        if "Data" in df.columns:
            ultima_data = pd.to_datetime(df["Data"].max(), dayfirst=True).date()
            hoje = datetime.today().date()

            if ultima_data != hoje:
                resposta = messagebox.askyesno(
                    "Confirmação",
                    f"As publicações em cache são do dia {ultima_data.strftime('%d/%m/%Y')}.\n"
                    f"Deseja rodar a automação utilizando o cache mesmo assim?\n"
                    "Se não, clique em não e delete a pasta Cache",
                )
                if not resposta:
                    kill_program()

    return df


def _montar_pacote_dados_npj(row: pd.Series, numero_unico: str, npj: str, processo: str) -> Dict[str, Any]:
    """
    Monta o dicionário `dados_npj` com os metadados do processo.

    Observação:
        O campo `"advogado"` inicia como `None` e só é preenchido dentro de
        `conferir_npj` caso a publicação realmente gere uma **nova distribuição**
        (criação de novo NPJ incidental), seguindo a ordem da lista de advogados.
    """
    outros = row["Outros"]

    polos = (
        ast.literal_eval(row["Ativos"]),
        ast.literal_eval(row["Passivos"]),
        [] if ast.literal_eval(row["Neutros"]) == ["Não informado pelo PJe"] else ast.literal_eval(row["Neutros"]),
    )

    return {
        "publicacao": row["CNJ Publicação"],
        "outros": outros,
        "processo": processo,
        "autuacao": row["Autuação"],
        "cnj": numero_unico,
        "polo": DICT_POLO[row["Polo"]],
        "tramitacao": row["Tramitacao"],
        "polos": polos,
        "advogado": None,
        "npj_principal": npj,
    }


def _registrar_erro_manual(
    df: pd.DataFrame,
    index: Any,
    erro: Exception,
    hora: Optional[str],
    matricula: Optional[str],
    mascara_npj: Optional[str],
    excel_automacao: str,
    publicacoes_txt: str,
    id_publicacao: str,
    log_widget: tk.Widget,
) -> None:
    """Registra falhas esperadas ou inesperadas como 'Manual: ...' na planilha e no cache."""
    if isinstance(erro, ErroEsperado):
        df.at[index, "Status"] = f"Manual: {erro}"
    else:
        _, _, exc_tb = sys.exc_info()
        tb = traceback.extract_tb(exc_tb)[-1]
        df.at[index, "Status"] = (
            f"Manual: Erro Inesperado: {erro} | Arquivo: {tb.filename} | Linha: {tb.lineno}"
        )

    df.at[index, "Hora"] = "Manual" if not hora else hora
    df.at[index, "Matrícula"] = matricula
    df.at[index, "NPJ"] = "Manual" if not mascara_npj else mascara_npj
    df.to_excel(excel_automacao, index=False)
    save_processed_publication(publicacoes_txt, id_publicacao)
    update_log(log_widget, f"Publicação colocada como Manual: {erro}\n")


# ------------------------------------------------------------------------------
# Fluxo Principal da Automação
# ------------------------------------------------------------------------------
def run_automation(log_widget: tk.Widget) -> None:
    """
    Executa todo o fluxo da automação de publicações do STJ.

    Etapas executadas:
        1. Preparação de cache (planilha Excel e arquivo de IDs processados).
        2. Abertura do navegador e login no portal.
        3. Leitura/obtenção de publicações (com cache).
        4. Coleta de dados das publicações no PJe (quando necessário).
        5. Conferência de BB-não-parte (descarte automático quando aplicável).
        6. Conferência do Número Único/NPJ e validação de partes.
        7. Indicação da publicação no sistema.
        8. Atualização do Excel (status, hora, matrícula, NPJ).
        9. Geração de relatório final e término.

    O progresso e os resultados são continuamente reportados no `log_widget`.

    Args:
        log_widget (tk.Widget): Widget de texto com *scroll* para exibir logs
            (por exemplo, `ScrolledText` do Tkinter).
    """
    update_log(log_widget, "Iniciando processo de automação\n\n")

    # 1) Preparação de cache e definição de caminhos
    update_log(log_widget, "Verificando cache...")
    publicacoes_txt, excel_automacao = cria_cache()

    # 2) Navegador + login manual
    driver, wait, matricula = _iniciar_sessao_portal(log_widget)

    # 3) Publicações (cache ou busca no portal)
    df = _carregar_ou_buscar_publicacoes(driver, excel_automacao, log_widget)

    # 4) Coleta de dados no PJe/CNJ (caso a coluna 'Polo' ainda esteja toda vazia)
    if df["Polo"].isnull().all():
        update_log(log_widget, "Buscando dados das publicações no STJ...")
        df = pegar_dados_cnj(driver, wait, df, log_widget)
        df.to_excel(excel_automacao, index=False)
    else:
        update_log(log_widget, "Dados das publicações já foram buscados anteriormente.")

    # Garante que o navegador está na URL base do portal necessária para usar a API
    ensure_base_url(driver)
    df["ID Publicação"] = df["ID Publicação"].astype(str)

    # Inicializa o distribuidor sequencial de advogados (embaralha a lista a cada execução)
    distribuidor_advogados = DistribuidorAdvogados(LISTA_ADVOGADOS)
    update_log(
        log_widget,
        f"Ordem sorteada de advogados para esta execução: {', '.join(distribuidor_advogados.advogados)}\n",
    )

    # 5–7) Loop de processamento individual por publicação
    for index, row in df.iterrows():
        hora: Optional[str] = None
        mascara_npj: Optional[str] = None
        id_publicacao = row["ID Publicação"]

        try:
            update_log(log_widget, f"Conferindo publicação {index + 1}/{len(df)} - {row['CNJ Publicação']}")

            # Lê o conjunto de publicações já marcadas como processadas
            publicacoes_processadas = load_processed_publications(publicacoes_txt)

            # Pula itens já processados; em casos específicos vindos do PJe, registra a razão
            if id_publicacao in publicacoes_processadas or pd.notna(row["Status"]):
                if id_publicacao not in publicacoes_processadas:
                    status_lower = row["Status"].lower()
                    if "segredo de justiça" in status_lower:
                        raise ErroEsperado("Processo em segredo de justiça")
                    elif "dados indefinidos" in status_lower:
                        raise ErroEsperado("Dados indefinidos")
                    elif "erro ao consultar processo" in status_lower:
                        raise ErroEsperado("Erro ao consultar processo")

                update_log(log_widget, f"Publicação já processada: {row['Status']}\n")
                continue

            # Normaliza o Número Único CNJ removendo pontuação
            numero_unico = row["Numero Único"].replace(".", "").replace("-", "")
            bb_parte = str(row["BB Parte"])
            cnj_publicacao = row["CNJ Publicação"]

            # 5) Descarte automático quando BB não é parte
            if bb_parte == "BB Não parte":
                update_log(log_widget, "Publicação BB não parte, conferindo possível descarte")
                bbnaoparte, quantidadenaoparte = conferir_bbnaoparte(
                    driver, numero_unico, id_publicacao, cnj_publicacao
                )
                time.sleep(1)

                if bbnaoparte:
                    update_log(
                        log_widget,
                        f"Publicação descartada como bb não parte automaticamente com histórico de {quantidadenaoparte}\n",
                    )
                    df.at[index, "Status"] = (
                        f"Descartada automaticamente como bb não parte, historico: {quantidadenaoparte}"
                    )
                    df.at[index, "Hora"] = time.strftime("%H:%M:%S")
                    df.at[index, "Matrícula"] = matricula
                    df.at[index, "NPJ"] = "BB Não Parte"
                    df.to_excel(excel_automacao, index=False)
                    save_processed_publication(publicacoes_txt, id_publicacao)
                    continue
                else:
                    update_log(log_widget, "Descarte automático não foi possível.")
                    raise ErroEsperado("BB Não Parte")

            # Verifica se o tipo de processo exige análise manual
            processo = row["Tipo Processo"].upper()
            if processo in TIPOS_PROCESSO_MANUAIS:
                update_log(log_widget, f"Publicação identificada como {processo}\n")
                raise ErroEsperado(processo)

            # 6) Conferência do Número Único e vínculo/cadastro/atualização de NPJ
            time.sleep(1)
            npj = conferir_numero_unico(driver, numero_unico).zfill(11)
            dados_npj = _montar_pacote_dados_npj(row, numero_unico, npj, processo)

            update_log(log_widget, "Conferindo npj...")
            id_npj, conferePartes, hora = conferir_npj(
                driver, dados_npj, distribuidor_advogados, log_widget
            )
            mascara_npj = montar_npj(driver, id_npj)

            # 7) Indicação ou descarte (Ata de Distribuição) da publicação no sistema
            update_log(log_widget, "Tratando publicação...")
            indicacao = indica_pubicacao(driver, id_publicacao, id_npj, log_widget)

            if indicacao:
                df.at[index, "Status"] = indicacao
                df.at[index, "Matrícula"] = matricula
                df.at[index, "Hora"] = hora
                df.at[index, "NPJ"] = mascara_npj
                df.to_excel(excel_automacao, index=False)
                update_log(log_widget, "Publicação tratada com sucesso!\n")

            if not conferePartes:
                raise ErroEsperado("Cadastro feito, arrumar partes")

            save_processed_publication(publicacoes_txt, id_publicacao)

        except Exception as e:
            _registrar_erro_manual(
                df,
                index,
                e,
                hora,
                matricula,
                mascara_npj,
                excel_automacao,
                publicacoes_txt,
                id_publicacao,
                log_widget,
            )

    # 8–9) Consolidação e geração de relatório final
    total_cadastradas = len(df[~df["Status"].str.startswith("Manual: ")])
    update_log(log_widget, f"\n\nFim das publicações.\nNúmero de publicações cadastradas: {total_cadastradas}")
    update_log(log_widget, "Gerando relatório...")
    dados_bbts = pegar_dados_tratadas_ultimos_7_dias(driver)
    driver.quit()

    gerar_relatorio(df, excel_automacao, publicacoes_txt, dados_bbts)
    update_log(log_widget, "Relatório gerado com sucesso, programa finalizado!")

    STOP_EVENT.set()


def start_automation(log_widget: tk.Widget) -> None:
    """Dispara a rotina de automação em *thread* separada.

    Mantém a interface gráfica responsiva enquanto a automação executa no
    *background* (dentro das possibilidades do processo atual).

    Args:
        log_widget (tk.Widget): Widget onde os logs serão exibidos.

    Returns:
        None
    """
    thread = threading.Thread(target=run_automation, args=(log_widget,))
    thread.start()


# ------------------------------------------------------------------------------
# Interface Gráfica (Tkinter)
# ------------------------------------------------------------------------------
class Interface:
    """Janela principal da aplicação de automação (Tkinter).

    Esta classe cria a janela principal, mostra instruções iniciais, controla
    os botões de ação e renderiza um painel de logs com *scroll* quando a
    automação é iniciada.

    Attributes:
        root (tk.Tk): Instância raiz da aplicação Tkinter.
        main_frame (tk.Frame): *Frame* externo da janela principal.
        inner_frame (tk.Frame): *Frame* interno (área amarela) com o conteúdo.
        instruction_label (tk.Label): Rótulo com instruções iniciais.
        start_button (tk.Button): Botão para iniciar a automação.
        final_button (tk.Button): Botão para finalizar o programa.
        log_widget (scrolledtext.ScrolledText): Área de logs (criada após iniciar).
        logo (PhotoImage): Logo opcional carregado do disco (se existir).
    """

    def __init__(self, root: tk.Tk) -> None:
        """Inicializa a interface principal.

        Args:
            root (tk.Tk): Janela raiz da aplicação.
        """
        self.root = root
        self.setup_ui()

    def setup_ui(self) -> None:
        """Configura layout, estilos e componentes iniciais da janela."""
        self.root.title("Automação de Publicações")
        self.root.geometry("740x720")
        self.root.configure(bg="#000080")  # Fundo azul escuro
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        # Frame principal com padding e cor de fundo
        self.main_frame = tk.Frame(self.root, bg="#000080", padx=10, pady=10)
        self.main_frame.grid(row=0, column=0, sticky="nsew")
        self.main_frame.rowconfigure(0, weight=1)
        self.main_frame.columnconfigure(0, weight=1)

        # Frame interno com fundo amarelo claro
        self.inner_frame = tk.Frame(self.main_frame, bg="#FFEE58", padx=10, pady=10)
        self.inner_frame.grid(row=0, column=0, sticky="nsew")
        self.inner_frame.columnconfigure(0, weight=1)

        # Tenta carregar e exibir a imagem; senão, exibe um texto substituto
        try:
            self.logo = PhotoImage(file=r"images\LBB.png").subsample(2, 2)
            tk.Label(self.inner_frame, image=self.logo, bg="#FFEE58").grid(row=0, column=0, pady=10)
        except Exception:
            tk.Label(
                self.inner_frame, text="Dijur", font=("Arial", 18, "bold"), bg="#FFEE58"
            ).grid(row=0, column=0, pady=10)

        # Label de boas-vindas
        tk.Label(
            self.inner_frame,
            text="Bem-vindo(a) ao sistema de automação do STJ",
            font=("Arial", 16),
            bg="#FFEE58",
            wraplength=500,
            justify="center",
        ).grid(row=1, column=0, pady=5)

        # Label com as instruções iniciais (serão removidas após clicar em "Iniciar Automação")
        instructions = (
            "- É recomendável não rodar a automação quando a internet ou o portal estiverem lentos ou instáveis.\n\n"
            "- Caso queira resetar o programa, ou em caso de algum erro estranho, exclua a pasta 'cache' e rode novamente.\n\n"
            "- O botão 'Iniciar Automação' inicia o processo de automação.\n\n"
            "- O botão 'Finalizar' encerra o programa imediatamente.\n\n"
            "- O botão 'Finalizar' deve ser usado apenas ao final do processo ou em casos de emergência.\n\n"
            "- Deve-se criar o atalho da equipe no drive; leia o arquivo 'instrucoes.txt' para mais informações."
        )
        self.instruction_label = tk.Label(
            self.inner_frame,
            text=instructions,
            font=("Arial", 14),
            bg="#FFEE58",
            wraplength=500,
            justify="left",
        )
        self.instruction_label.grid(row=2, column=0, pady=10)

        # Botão "Iniciar Automação"
        self.start_button = tk.Button(
            self.inner_frame,
            text="Iniciar Automação",
            font=("Arial", 12, "bold"),
            bg="green",
            fg="white",
            relief=tk.RAISED,
            command=self.confirmar_selecao,
        )
        self.start_button.grid(row=3, column=0, pady=15, ipadx=20)

    def confirmar_selecao(self) -> None:
        """Ação do botão **Iniciar Automação**."""
        self.instruction_label.destroy()
        self.start_button.destroy()

        # Cria o widget de log vazio com scroll
        self.log_widget = scrolledtext.ScrolledText(
            self.inner_frame,
            state="normal",
            width=90,
            height=20,
            font=("Arial", 12),
        )
        self.log_widget.grid(row=2, column=0, pady=10, sticky="nsew")
        self.log_widget.config(state="disabled")

        # Botão "Finalizar" para encerrar o programa
        self.final_button = tk.Button(
            self.inner_frame,
            text="Finalizar",
            font=("Arial", 12, "bold"),
            bg="red",
            fg="white",
            relief=tk.RAISED,
            command=kill_program,
        )
        self.final_button.grid(row=3, column=0, pady=15, ipadx=20)

        # Inicia o processo de automação (em thread separada)
        start_automation(self.log_widget)


def main() -> None:
    """Ponto de entrada da aplicação GUI."""
    root = tk.Tk()
    try:
        root.iconbitmap(r"images\IcoBB.ico")
    except Exception:
        pass
    app = Interface(root)
    root.mainloop()


if __name__ == "__main__":
    main()
