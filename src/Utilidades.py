import mysql.connector
import json
import logging
from logging.handlers import QueueHandler, QueueListener
from queue import Queue
import os
import sys

def buscar_cameras(config):
    db = config["database"]

    try:
        conn = mysql.connector.connect(
            host=db["host"],
            user=db["user"],
            password=db["password"],
            database=db["database"]
        )
        cameras = config["cameras"]["zm_monitor_id"]
        ids_cameras = ','.join(map(str, cameras))

        cursor = conn.cursor()
        cursor.execute(f"SELECT cameras.id as idcamera, cameras.nome as nomecamera, cameras.zm_id_monitor, cameras.modelo, cameras.ptz_ip, cameras.ptz_user, cameras.ptz_password, cameras.ptz_delay_stream, cameras.ptz_ctrl_ativo, cameras.duracaobusca, cameras.duracaoconfirmacao, cameras.y_conf, cameras.y_iou, cameras.perc_frames_para_mp4, cameras.fps, cameras.width, cameras.height, instituicoes.nome as nomeinstituicao, instituicoes.chat_id_telegram, zm_servers.url, zm_servers.usuario, zm_servers.senha FROM cameras JOIN zm_servers ON cameras.zm_servers_id = zm_servers.id JOIN instituicoes ON cameras.instituicoes_id = instituicoes.id WHERE cameras.zm_id_monitor IN ({ids_cameras});")

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        resultados = []
        for row in rows:
            data = dict(zip(columns, row))
            resultados.append(data)

        return resultados

    except mysql.connector.Error as err:
        print(f"Erro ao conectar ou executar a consulta: {err}")
        return []

    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# Criação da queue para o log
log_queue = Queue()
formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(threadName)s] [%(nome_camera)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler = logging.FileHandler('/var/log/aof_ng_d.log', mode='a')
file_handler.setFormatter(formatter)
listener = QueueListener(log_queue, file_handler)
listener.start()

def configurar_logger(nome_camera, nome_logger="AoFLogger"):
    logger_name = f"{nome_logger}.{nome_camera}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(isinstance(h, QueueHandler) for h in logger.handlers):
        queue_handler = QueueHandler(log_queue)
        logger.addHandler(queue_handler)

    return logging.LoggerAdapter(logger, extra={"nome_camera": nome_camera})

