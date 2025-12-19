import os
import time
import requests
import shutil
import logging
from sdnotify import SystemdNotifier
import json
#import datetime
from datetime import datetime
import pytz
import mysql.connector

# Carrega a configuração
CONFIG_FILE = "/usr/local/src/AoFNGD/src/config.json"
with open(CONFIG_FILE, "r") as f:
    config_sys = json.load(f)

# CONFIGURAÇÕES
WATCH_DIR = config_sys["locais"]["diretorio_videos"]
RETRY_DELAY = 10  # segundos entre tentativas

# CONFIGURAÇÃO DE LOG
LOG_FILE = "/var/log/envia_notificacao.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Comunicação com whatchdog
notifier = SystemdNotifier()

# Garante que o diretório de enviados exista
os.makedirs(WATCH_DIR, exist_ok=True)

def busca_notificacoes(deteccao_id, host=config_sys["database"]["host"], user=config_sys["database"]["user"], password=config_sys["database"]["password"], database=config_sys["database"]["database"]):
    try:
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database
        )
        cursor = conn.cursor()

        query = """
            SELECT COUNT(*) AS nNotificacoes
            FROM notificacoes 
            WHERE datahora_visto IS NOT NULL
            AND deteccoes_id IN (
                SELECT d1.id
                FROM deteccoes d1
                JOIN deteccoes d2 ON d2.id = %s
                JOIN processamentos p1 ON p1.id = d1.processamentos_id
                JOIN processamentos p2 ON p2.id = d2.processamentos_id
                WHERE d1.datahora >= NOW() - INTERVAL 30 MINUTE
                AND p1.cameras_id = p2.cameras_id
                AND (
                    p1.coordenadas_cameras_id = p2.coordenadas_cameras_id
                    OR (p1.coordenadas_cameras_id IS NULL AND p2.coordenadas_cameras_id IS NULL)
                )
            );
        """

        values = (deteccao_id,)
        cursor.execute(query, values)
        result = cursor.fetchone()
        nNotificacoes = result[0] if result else 0
        return nNotificacoes

    except mysql.connector.Error as e:
        logging.exception(f"Falha ao consultar notificações no MySQL: {e}")
        return None

    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

def input_notificacao( deteccao_id, host=config_sys["database"]["host"], user=config_sys["database"]["user"], password=config_sys["database"]["password"], database=config_sys["database"]["database"]): 
    try:
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database
        )
        cursor = conn.cursor()
        
        query = """
            INSERT INTO notificacoes (deteccoes_id, datahora_abertura) VALUES (%s, NOW())
        """

        values = (deteccao_id,)
        
        cursor.execute(query, values)
        conn.commit()

        # Retorna o ID do registro inserido
        return cursor.lastrowid 
    except mysql.connector.Error as e:
        logging.exception(f"Falha ao inserir notificação no MySQL: {e}")

    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

def input_deteccao( processamentos_id, datahora, mp4, mp4combbox, metadados, host=config_sys["database"]["host"], user=config_sys["database"]["user"], password=config_sys["database"]["password"], database=config_sys["database"]["database"]):
    try:
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database
        )
        cursor = conn.cursor()

        query = """
            INSERT IGNORE INTO deteccoes (processamentos_id, datahora, mp4, mp4combbox, metadados) VALUES (%s, %s, %s, %s, %s)
        """
        
        values = (processamentos_id, datahora, mp4, mp4combbox, metadados)

        cursor.execute(query, values)
        conn.commit()

#        print("DEBUG - Query completa:", query % tuple(values))
#        print("DEBUG - Mensagens do servidor:", cursor._warnings)
#        print("DEBUG - Mensagens do servidor:", conn.info())

        # Retorna o ID do registro inserido
        return cursor.lastrowid
    except mysql.connector.Error as e:
        logging.exception(f"Falha ao inserir detecção no MySQL: {e}")

    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

def get_meta_data(file_path_meta):
    try:
        with open(file_path_meta, "r", encoding="utf-8") as f:
            meta_data = json.load(f)
            return meta_data
    except Exception as e:
        logging.error(f"Erro ao carregar {file_path_meta}: {e}")
        meta_data = {}

def send_video_to_telegram(file_path, file_path_meta, file_path_wbbox, meta_data, notificacao_id):
    BOT_TOKEN = config_sys["telegram"]["bot_token"]
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
  
    dt = datetime.fromtimestamp(meta_data.get('timestamp', ''), pytz.UTC)
    dt_brasil = dt.astimezone(pytz.timezone("America/Sao_Paulo"))

    caption = (
        f"<b>Câmera:</b> {meta_data.get('nomecamera', '')}\n"
        f"<b>Horário:</b> {dt_brasil.strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"<b>Q. Analisados:</b> {meta_data.get('analyzed_frames', '')}\n"
        f"<b>Q. Detectados:</b> {meta_data.get('detection_count', '')}\n"
    )
    if "ptz_atual" in meta_data:
        caption += (
            f"<b>Localização:</b> {meta_data.get('desccoordenadas_cameras', '')}\n"
            f"<b>PTZ:</b> {meta_data.get('ptz_atual', '')}\n"
            f"<b>PTZ Ajustado:</b> {meta_data.get('ptz_ajustado', '')}\n"
            f"<b>BBox:</b> {meta_data.get('bbox_maior_score', '')}\n"
        )
    caption = caption[:1024]

    while True:
        logging.info(f"Enviando aqui para o telegram: {file_path}")
        try:
            with open(file_path_wbbox, "rb") as video:
                files = {"video": video}
                data = {
                    "chat_id": meta_data.get('chat_id_telegram', ''),
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps({
                        "inline_keyboard": [
                            [
                                {
                                    "text": "Visto",
                                    "callback_data": f"ntfvisto|{notificacao_id}"
                                }
                            ]
                        ]
                    })
                }
                response = requests.post(url, files=files, data=data, timeout=60)
            if response.status_code == 200:
                logging.info(f"Enviado com sucesso: {file_path}")
                return True
            else:
                logging.error(f"Erro ao enviar {file_path}: {response.status_code} - {response.text}")
        except Exception as e:
            logging.exception(f"Exceção ao tentar enviar {file_path}: {e}")
        logging.info(f"Tentando novamente em {RETRY_DELAY} segundos...")
        time.sleep(RETRY_DELAY)

def handle_mp4_triplet(base_file_path, wait_time=3):
    # Aguarda para garantir que todos os arquivos estejam completos
    time.sleep(wait_time)

    base_name, ext = os.path.splitext(base_file_path)
    if base_name.endswith("_wbbox"):
        return  # Ignora arquivos _wbbox.mp4

    file_path_meta = f"{base_name}_meta.txt"
    file_path_wbbox = f"{base_name}_wbbox.mp4"

    # Obtendo os metadados em JSON
    meta_data = get_meta_data( file_path_meta )

    # Diretório de destino
    SENT_DIR = config_sys["locais"]["diretorio_videos"] + "/" + meta_data.get('nomecamera', '')

    # Garante que o diretório de enviados exista
    os.makedirs(SENT_DIR, exist_ok=True)

    logging.info(f"Processando arquivo: {base_file_path}")

    if not (os.path.exists(base_file_path) and os.path.exists(file_path_meta) and os.path.exists(file_path_wbbox)):
        logging.warning("Um ou mais arquivos relacionados não foram encontrados.")
        return

    try:
        if "ptz_atual" in meta_data:
            coordenadas_cameras_id = int(meta_data.get('idcoordenadas_cameras', ''))
        else:
            coordenadas_cameras_id = None
        cameras_id = int(meta_data.get('idcamera', ''))
        processamentos_id = int(meta_data.get('processamentos_id', ''))
        datahora = datetime.fromtimestamp(meta_data.get('timestamp', ''))
        mp4 = meta_data.get('nomecamera', '') + "/" + base_file_path.replace(WATCH_DIR+'/', '')
        mp4combbox = meta_data.get('nomecamera', '') + "/" + file_path_wbbox.replace(WATCH_DIR+'/', '')
        metadados = json.dumps(meta_data)
 
        deteccaoid = input_deteccao(processamentos_id, datahora, mp4, mp4combbox, metadados)

        if deteccaoid != 0:
            if busca_notificacoes(deteccaoid) == 0:
                notificacao_id = input_notificacao(deteccaoid)
                if send_video_to_telegram(base_file_path, file_path_meta, file_path_wbbox, meta_data, notificacao_id):
                    logging.info(f"Registrando a notificação para a Detecção ID: {deteccaoid}")

                    dest_path = os.path.join(SENT_DIR, os.path.basename(base_file_path))
                    shutil.move(base_file_path, dest_path)
                    logging.info(f"Arquivo movido para: {dest_path}")

                    dest_path_meta = os.path.join(SENT_DIR, os.path.basename(file_path_meta))
                    shutil.move(file_path_meta, dest_path_meta)
                    logging.info(f"Meta movido para: {dest_path_meta}")

                    dest_path_wbbox = os.path.join(SENT_DIR, os.path.basename(file_path_wbbox))
                    shutil.move(file_path_wbbox, dest_path_wbbox)
                    logging.info(f"WBBox movido para: {dest_path_wbbox}")
                else:
                    logging.error(f"Falha ao enviar notificação telegram para a Detecção ID: {deteccaoid}")
            else:
                logging.info(f"Movendo os arquivos sem notificação.")

                dest_path = os.path.join(SENT_DIR, os.path.basename(base_file_path))
                shutil.move(base_file_path, dest_path)
                logging.info(f"Arquivo movido para: {dest_path}")

                dest_path_meta = os.path.join(SENT_DIR, os.path.basename(file_path_meta))
                shutil.move(file_path_meta, dest_path_meta)
                logging.info(f"Meta movido para: {dest_path_meta}")

                dest_path_wbbox = os.path.join(SENT_DIR, os.path.basename(file_path_wbbox))
                shutil.move(file_path_wbbox, dest_path_wbbox)
                logging.info(f"WBBox movido para: {dest_path_wbbox}")

        logging.info(f"Final do Processamento do Detecção ID: {deteccaoid}")
    except Exception as e:
        logging.exception(f"Falha ao enviar para gravação no Banco de Dados: {e}")
        os._exit(1)

def process_existing_files():
    mp4_files = [
        os.path.join(WATCH_DIR, f)
        for f in os.listdir(WATCH_DIR)
        if f.endswith(".mp4")
    ]

    # Ordena por data de modificação (mais antigos primeiro)
    mp4_files.sort(key=lambda f: os.path.getmtime(f))

    for file_path in mp4_files:
        handle_mp4_triplet(file_path)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.info("Iniciando verificação periódica de arquivos...")

    try:
        while True:
            process_existing_files()
            time.sleep(1)
            notifier.notify("WATCHDOG=1")
    except Exception as e:
        logging.info("Falha durante a verificação: {e}.")
