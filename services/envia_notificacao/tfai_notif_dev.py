# time para medir durações e intervalos entre rotinas
import time
# logging para registrar funcionamento/erros
import logging
# os para operações de arquivo (excluir logs anteriores)
import os
# pandas para manipulação de tabelas em memória
import pandas as pd
# conector MySQL para acesso ao banco
import mysql.connector
# systemd notifier para watchdog (mantém serviço "vivo")
from sdnotify import SystemdNotifier
# Funções de negócio para filtros/agrupamentos em dados
import filters_functions as filters
# Funções de integração (Telegram/push)
import platform_functions as platforms
# Funções auxiliares de SQL e URLs assinadas
import sql_functions
# traceback para registrar pilha em caso de erro
import traceback

# Instancia o notificador do systemd
WATCHDOG = SystemdNotifier()

# Nome do banco de dados e credenciais
DB_NAME="AoFessays"
DB_USER="AoFessays"
DB_PASS="UMUStK-y[Ui3NfOF"
DB_HOST="10.0.5.103"

# Nomes dos arquivos de log
log_file = "notifications.log"
results_file = "results.log"
debug_file = "debug.log"
#! Remove o log anterior, se existir -> REMOVER AO COLOCAR EM PRODUÇÃO
# Em ambiente de desenvolvimento, limpa arquivos de log para começar do zero
if os.path.exists(log_file) and os.path.exists(results_file):
    os.remove(log_file)
    os.remove(results_file)
    os.remove(debug_file)

# Inicializa loggers com níveis
results = logging.getLogger(results_file)
results.setLevel(logging.INFO) 
log = logging.getLogger(log_file)
log.setLevel(logging.INFO)
debug = logging.getLogger(debug_file)
debug.setLevel(logging.INFO)

# Handlers que escrevem em arquivo
file_handler_results = logging.FileHandler(results_file)
file_handler_log = logging.FileHandler(log_file)
file_handler_debug = logging.FileHandler(debug_file)

# Formatação das mensagens
formatter = logging.Formatter(
                            '[%(asctime)s] - %(levelname)s - %(message)s',
                            datefmt='%d-%b-%Y %H:%M:%S'
                            )

# Aplica o formatter aos handlers
file_handler_results.setFormatter(formatter)
file_handler_log.setFormatter(formatter)
file_handler_debug.setFormatter(formatter)

# Adiciona handlers aos loggers
results.addHandler(file_handler_results)
log.addHandler(file_handler_log)
debug.addHandler(file_handler_debug)
# 🔴 [TESTE TEMPORÁRIO] Variável global para controlar a data/hora do último evento enviado
LAST_EVENT_DATETIME = None

#* ======================================== FUNÇÕES DE EXECUÇÃO PRINCIPAL ======================================== *#
def notifications_routine():
    # Usa a variável global (para filtro temporal opcional)
    global LAST_EVENT_DATETIME  # 🔴 [TESTE TEMPORÁRIO]
    # Notifica o watchdog para indicar atividade
    WATCHDOG.notify("WATCHDOG=1")
    # Aguarda até obter conectividade à internet
    while not sql_functions.check_online_status():
        log.warning("Sem conexão com a internet. Tentando novamente em 5 segundos...")
        time.sleep(5)
    
    # Abre conexão com o banco MySQL
    conexao = mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )

    try:
        #* Importar as tabelas do banco de dados
        # Lê detecções/eventos/links e servidores
        tabela_detections_pd, tabela_eventos_pd, tabela_detections_eventos_pd, zm_servers_pd = sql_functions.read_database(conexao)

        # Verifica leitura bem-sucedida
        if tabela_detections_pd is None:
            log.warning("Erro ao ler os dados do banco de dados.")
            return False
        else:
            log.info("Dados lidos com sucesso.")
        
        # Configura opções de exibição do pandas
        pd.set_option('display.max_rows', None)
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 0)

        #* ====================================== FILTRAGEM DAS TABELAS ============================= *#
        # Garante tipo datetime em 'datahora'
        tabela_detections_pd['datahora'] = pd.to_datetime(tabela_detections_pd['datahora'])
        # Ordena desc para priorizar as mais recentes
        tabela_detections_pd = tabela_detections_pd.sort_values('datahora', ascending=False)
        # Marca repetições por janela por câmera
        tabela_detections_pd = filters.identify_repetition(tabela_detections_pd)
        '''
        colunas_excluir = ["mp4", "mp4combbox"]
        log_str = tabela_detections_pd.drop(columns=colunas_excluir, errors="ignore").to_string()
        debug.info("Tabela DETECTIONS após identificar repetições:")
        debug.info(f'\n{log_str}')'''
        # Agrupa detecções em eventos e atualiza tabela de links
        tabela_eventos_pd, tabela_detections_eventos_pd = filters.agrupar_deteccoes_em_eventos(
            tabela_detections_pd, tabela_eventos_pd, tabela_detections_eventos_pd)

        # Substitui NaN por None para salvar em DB
        tabela_eventos_pd = tabela_eventos_pd.where(pd.notna(tabela_eventos_pd), None)
        tabela_detections_eventos_pd = tabela_detections_eventos_pd.where(pd.notna(tabela_detections_eventos_pd), None)

        # Seleciona eventos pendentes de notificação
        detections_to_notify = filters.get_eventos_para_notificar(tabela_eventos_pd)

        # 🔴 [TESTE TEMPORÁRIO] Ignora eventos anteriores ao último enviado (se definido)
        if LAST_EVENT_DATETIME is not None:
            detections_to_notify = detections_to_notify[
                pd.to_datetime(detections_to_notify['datahora_abertura']) > LAST_EVENT_DATETIME
            ]

        #if len(detections_to_notify) > 15:
            #detections_to_notify = detections_to_notify.tail(5)
        #* ====================================== CONEXÃO COM A API DO AoFessays ============================= *#
        # Extrai o base_url do file server
        file_server = f"{zm_servers_pd['file_server'].iloc[0]}"
        # Somente executa se há algo para notificar
        if not detections_to_notify.empty:
            # 🔴 [TESTE TEMPORÁRIO] Seleciona apenas o evento mais recente (por datahora_abertura)
            detections_to_notify = detections_to_notify.sort_values('datahora_abertura', ascending=False).head(1)
            log.info(f"Encontradas {len(detections_to_notify)} notificações para enviar.")            
            # Itera apenas sobre o mais recente (após head(1))
            for idx, entry in detections_to_notify.iterrows():
                # Notifica o watchdog
                WATCHDOG.notify("WATCHDOG=1")
                # Busca a primeira detecção atrelada ao evento
                deteccao_id = filters.get_deteccao_id_por_evento_id(tabela_detections_eventos_pd, entry['id'])
                # Extrai a linha da detecção
                deteccao = tabela_detections_pd.loc[tabela_detections_pd['id'] == deteccao_id].squeeze()
                # Caminho do thumbnail
                image = (deteccao['thumbnail'])
                # Imprime no console para depuração
                print(f'Video: {deteccao["mp4"]}')
                print(f"Thumbnail: {image}")
                # Gera URLs assinadas (para imagem e vídeo)
                signed_url = sql_functions.generate_signed_url(file_server, image)
                video_signed_url = sql_functions.generate_signed_url(file_server, deteccao['mp4'])
                print(f"Signed URL: {signed_url}")
                print(f"Video Signed URL: {video_signed_url}")
                # Determina o chat_id_telegram para a câmera em questão
                chatid = sql_functions.get_chatid_by_camera_name(deteccao, conexao)
                # Envia para o Telegram (imagem/thumbnail)
                telegram_result = platforms.enviar_notificacao_telegram(deteccao, signed_url,chatid)
                # Envia push
                push_result = platforms.enviar_notificacao_push(entry)
                
                # Marca como notificado se qualquer envio deu certo
                if telegram_result['status'] == 'success' or push_result['status'] == 'success':
                    if telegram_result['status'] == 'success':
                        tempo = pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%Y-%m-%d %H:%M:%S')
                        tabela_eventos_pd.loc[tabela_eventos_pd['id'] == entry['id'], 'datahora_notificado'] = tempo
                        log.info(f"Notificação para o evento {entry['id']} marcada como entregue com sucesso.")

                    if push_result['status'] == 'success':
                        tempo = pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%Y-%m-%d %H:%M:%S')
                        tabela_eventos_pd.loc[tabela_eventos_pd['id'] == entry['id'], 'datahora_notificado'] = tempo
                        log.info(f"Notificação push para o evento {entry['id']} entregue com sucesso.")
                    # 🔴 [TESTE TEMPORÁRIO] Atualiza marcador temporal
                    LAST_EVENT_DATETIME = pd.to_datetime(entry['datahora_abertura'])
                    log.info(f"Última data/hora de evento enviada atualizada para: {LAST_EVENT_DATETIME}")
            # Persiste as alterações no banco (novos eventos e links)
            database_save = filters.save_to_database(tabela_eventos_pd, tabela_detections_eventos_pd, conexao) 
            # Logs detalhados das tabelas para depuração
            results.info("Tabelas de Debug:")
            results.info("Topo da tabela DETECTIONS:")
            results.info(f'\n{tabela_detections_pd.head(15)}')
            results.info("Base da tabela DETECTIONS:")
            results.info(f'\n{tabela_detections_pd.tail(5)}')
            results.info("Topo da tabela EVENTOS:")
            results.info(f'\n{tabela_eventos_pd.head(15)}')
            results.info("Base da tabela EVENTOS:")
            results.info(f'\n{tabela_eventos_pd.tail(5)}')
            results.info("Topo da tabela DETECTIONS_EVENTOS:")
            results.info(f'\n{tabela_detections_eventos_pd.head(15)}')
            results.info("Base da tabela DETECTIONS_EVENTOS:")
            results.info(f'\n{tabela_detections_eventos_pd.tail(5)}')
            results.info("Eventos a serem notificados:")
            results.info(f'\n{detections_to_notify}')
        
        else:
            # Nenhum evento a notificar neste ciclo
            log.info('Sem novas operações realizadas pelo sistema de notificações')

    except Exception as e:
        # Em caso de exceção, registra o stack trace completo
        tb_str = traceback.format_exc()
        log.error(f"Ocorreu um erro inesperado e o programa vai continuar: {e}\nTraceback:\n{tb_str}")
        return False
    finally:
        # Finalização do ciclo e fechamento da conexão
        print('Done')
        if conexao and conexao.is_connected():
            conexao.close()

def main():
    # Loop contínuo: roda rotina, mede duração e dorme 15s
    while True:
        time_start = time.time()
        notifications_routine()
        time_finish = time.time()
        log.info(f'Elapsed time for notifications routine: {time_finish - time_start} seconds')
        time.sleep(15)

# Execução direta (ambiente de desenvolvimento)
if __name__ == "__main__":
    # --- INÍCIO DA MODIFICAÇÃO ---

    # Importa função de mensagem simples e CHAT_ID para sinalizar startup no Telegram
    from platform_functions import enviar_mensagem_simples_telegram, CHAT_ID_TELEGRAM
    # Importa datetime para compor timestamp humano
    from datetime import datetime

    # Prepara mensagem de inicialização do bot
    timestamp_inicio = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    mensagem_startup = (
        f"✅ *Bot de Notificações TFAI Iniciado*\n\n"
        f"▪️ *Status:* Online e monitorando.\n"
        f"▪️ *Início:* {timestamp_inicio}"
    )
    
    # Envia a mensagem para o chat principal de monitoramento
    print("Enviando mensagem de inicialização para o Telegram...")
    enviar_mensagem_simples_telegram(mensagem_startup, CHAT_ID_TELEGRAM)
    
    # --- FIM DA MODIFICAÇÃO ---

    # Continua com o fluxo normal
    LAST_EVENT_DATETIME = None
    main()