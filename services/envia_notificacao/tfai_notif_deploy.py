# time para medir tempo de execução e controlar intervalos
import time
# logging para registrar status e erros
import logging
# pandas para manipular dados tabulares
import pandas as pd
# conector MySQL para comunicação com o banco
import mysql.connector
# sdnotify para sinalizar watchdog do systemd (saúde do serviço)
from sdnotify import SystemdNotifier
# Importa funções de filtragem e agrupamento de detecções/eventos
import filters_functions as filters
# Importa funções de integração com plataformas (Telegram, push)
import platform_functions as platforms
# Funções auxiliares de SQL (leitura, URL assinada, etc.)
import sql_functions
# traceback para obter pilha detalhada em caso de exceção
import traceback

# Instância do notificador para o watchdog do systemd
WATCHDOG = SystemdNotifier()

# Nome do banco de dados e credenciais
DB_NAME="AoFessays"
DB_USER="AoFessays"
DB_PASS="UMUStK-y[Ui3NfOF"
DB_HOST="10.0.5.103"

# Nome dos arquivos de log
log_file = "notifications.log"
results_file = "results.log"
debug_file = "debug.log"

# Configuração dos loggers
results = logging.getLogger(results_file)
results.setLevel(logging.INFO) 
log = logging.getLogger(log_file)
log.setLevel(logging.INFO)
debug = logging.getLogger(debug_file)
debug.setLevel(logging.INFO)

# Handlers de arquivo para cada logger
file_handler_results = logging.FileHandler(results_file)
file_handler_log = logging.FileHandler(log_file)
file_handler_debug = logging.FileHandler(debug_file)

# Formatter padrão (data, nível, mensagem)
formatter = logging.Formatter(
                            '[%(asctime)s] - %(levelname)s - %(message)s',
                            datefmt='%d-%b-%Y %H:%M:%S'
                            )

# Aplica o formatter aos handlers
file_handler_results.setFormatter(formatter)
file_handler_log.setFormatter(formatter)
file_handler_debug.setFormatter(formatter)

# Associa handlers aos loggers
results.addHandler(file_handler_results)
log.addHandler(file_handler_log)
debug.addHandler(file_handler_debug)
# 🔴 [TESTE TEMPORÁRIO] Variável global para controlar a data/hora do último evento enviado
LAST_EVENT_DATETIME = None

#* ======================================== FUNÇÕES DE EXECUÇÃO PRINCIPAL ======================================== *#
def notifications_routine():
    # Declara o uso da variável global para atualização
    global LAST_EVENT_DATETIME  # 🔴 [TESTE TEMPORÁRIO]
    # Notifica o watchdog do systemd (mantém serviço "vivo")
    WATCHDOG.notify("WATCHDOG=1")
    # Aguarda até ter conectividade com a internet
    while not sql_functions.check_online_status():
        log.warning("Sem conexão com a internet. Tentando novamente em 5 segundos...")
        time.sleep(5)
    
    # Abre conexão com o MySQL usando as credenciais definidas
    conexao = mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )

    try:
        #* Importar as tabelas do banco de dados
        # Lê detections, eventos, links detections_eventos e zm_servers
        tabela_detections_pd, tabela_eventos_pd, tabela_detections_eventos_pd, zm_servers_pd = sql_functions.read_database(conexao)

        # Se houve erro na leitura do banco, retorna False para indicar falha
        if tabela_detections_pd is None:
            log.warning("Erro ao ler os dados do banco de dados.")
            return False
        else:
            log.info("Dados lidos com sucesso.")
        
        # Ajustes de exibição do pandas (para logs ou depuração)
        pd.set_option('display.max_rows', None)
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 0)

        #* ====================================== FILTRAGEM DAS TABELAS ============================= *#
        # Converte a coluna 'datahora' para datetime
        tabela_detections_pd['datahora'] = pd.to_datetime(tabela_detections_pd['datahora'])
        # Ordena detecções por data/hora descrescente (recente primeiro)
        tabela_detections_pd = tabela_detections_pd.sort_values('datahora', ascending=False)
        # Identifica repetições por janela de tempo por câmera
        tabela_detections_pd = filters.identify_repetition(tabela_detections_pd)

        # Remove colunas pesadas para gerar uma string de log mais limpa (mp4/combbox)
        colunas_excluir = ["mp4", "mp4combbox"]
        log_str = tabela_detections_pd.drop(columns=colunas_excluir, errors="ignore").to_string()
        # Agrupa detecções em eventos e cria/atualiza tabela de links
        tabela_eventos_pd, tabela_detections_eventos_pd = filters.agrupar_deteccoes_em_eventos(
            tabela_detections_pd, tabela_eventos_pd, tabela_detections_eventos_pd)

        # Converte NaN para None para compatibilidade com writes no banco
        tabela_eventos_pd = tabela_eventos_pd.where(pd.notna(tabela_eventos_pd), None)
        tabela_detections_eventos_pd = tabela_detections_eventos_pd.where(pd.notna(tabela_detections_eventos_pd), None)

        # Seleciona eventos que ainda não foram notificados (com possível corte de data)
        detections_to_notify = filters.get_eventos_para_notificar(tabela_eventos_pd)

        '''
        # 🔴 [TESTE TEMPORÁRIO] Ignorar eventos anteriores ao último enviado por datahora_abertura
        if LAST_EVENT_DATETIME is not None:
            detections_to_notify = detections_to_notify[
                pd.to_datetime(detections_to_notify['datahora_abertura']) > LAST_EVENT_DATETIME
            ]
        '''
        #if len(detections_to_notify) > 15:
            #detections_to_notify = detections_to_notify.tail(5)
        
        #* ====================================== CONEXÃO COM A API DO AoFessays ============================= *#
        # Extrai a base de URL do file server (para gerar as URLs assinadas)
        file_server = f"{zm_servers_pd['file_server'].iloc[0]}"
        # Itera sobre todos os eventos a notificar
        for idx, entry in detections_to_notify.iterrows():
            # Re-notifica o watchdog do systemd em cada iteração (atividade)
            WATCHDOG.notify("WATCHDOG=1")
            # Descobre a primeira detecção (id) ligada a este evento
            deteccao_id = filters.get_deteccao_id_por_evento_id(tabela_detections_eventos_pd, entry['id'])
            # Recupera a linha correspondente da detecção
            deteccao = tabela_detections_pd.loc[tabela_detections_pd['id'] == deteccao_id].squeeze()
            # Obtém o caminho do thumbnail
            image = (deteccao['thumbnail'])
            # Gera URL assinada temporária para este thumbnail
            signed_url = sql_functions.generate_signed_url(file_server, image)
            # Recupera o chat_id_telegram da instituição associada à câmera
            chatid = sql_functions.get_chatid_by_camera_name(deteccao, conexao)
            # Envia notificação para o Telegram (com thumbnail)
            telegram_result = platforms.enviar_notificacao_telegram(deteccao, signed_url, chatid)
            # Envia notificação push para o backend/aplicativos
            push_result = platforms.enviar_notificacao_push(entry)
            
            # Se qualquer uma das notificações foi bem-sucedida, marca como notificado
            if telegram_result['status'] == 'success' or push_result['status'] == 'success':
                if telegram_result['status'] == 'success':
                    tempo = pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%Y-%m-%d %H:%M:%S')
                    tabela_eventos_pd.loc[tabela_eventos_pd['id'] == entry['id'], 'datahora_notificado'] = tempo
                    log.info(f"Notificação para o evento {entry['id']} marcada como entregue com sucesso.")

                if push_result['status'] == 'success':
                    tempo = pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%Y-%m-%d %H:%M:%S')
                    tabela_eventos_pd.loc[tabela_eventos_pd['id'] == entry['id'], 'datahora_notificado'] = tempo
                    log.info(f"Notificação push para o evento {entry['id']} entregue com sucesso.")
                # 🔴 [TESTE TEMPORÁRIO] Atualiza a data/hora do último evento enviado
                LAST_EVENT_DATETIME = pd.to_datetime(entry['datahora_abertura'])
                log.info(f"Última data/hora de evento enviada atualizada para: {LAST_EVENT_DATETIME}")
            # Persiste as tabelas atualizadas no banco (novos eventos e links)
            database_save = filters.save_to_database(tabela_eventos_pd, tabela_detections_eventos_pd, conexao)  
            # Logs de debug/resultado das tabelas para inspeção
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
            # Se o loop não executou (DataFrame vazio), informa que não houve trabalho
            log.info('Sem novas operações realizadas pelo sistema de notificações')

    except Exception as e:
        # Em caso de erro, registra a stack trace e retorna False
        tb_str = traceback.format_exc()
        log.error(f"Ocorreu um erro inesperado e o programa vai continuar: {e}\nTraceback:\n{tb_str}")
        return False
    finally:
        # Indica término da rotina no console e fecha a conexão se aberta
        print('Done')
        if conexao and conexao.is_connected():
            conexao.close()

def main():
    # Loop infinito: executa a rotina, mede tempo, aguarda 15s e repete
    while True:
        time_start = time.time()
        notifications_routine()
        time_finish = time.time()
        log.info(f'Elapsed time for notifications routine: {time_finish - time_start} seconds')
        time.sleep(15)

# Ponto de entrada do script em produção
if __name__ == "__main__":
    # 🔴 [TESTE TEMPORÁRIO] Inicializa a variável global
    LAST_EVENT_DATETIME = None
    main()