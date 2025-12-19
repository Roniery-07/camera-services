from abc import ABC, abstractmethod
from AoFBase import AoFBase
import mysql.connector
import json
import time

# Subclasse de Câmeras Fixas
class CamBaseFixa(AoFBase):
    def atualiza_dados_camera_fixa(self, config_sys, config_cam):
        db = config_sys["database"]

        try:
            conn = mysql.connector.connect(
                host=db["host"],
                user=db["user"],
                password=db["password"],
                database=db["database"]
            )

            id_camera = config_cam["zm_id_monitor"]
            cursor = conn.cursor()
            cursor.execute(f"SELECT cameras.id as idcamera, cameras.nome as nomecamera, cameras.zm_id_monitor, cameras.modelo, cameras.duracaobusca, cameras.duracaoconfirmacao, cameras.y_conf, cameras.y_iou, cameras.perc_frames_para_mp4, cameras.fps, cameras.width, cameras.height, instituicoes.nome as nomeinstituicao, instituicoes.chat_id_telegram, zm_servers.url, zm_servers.usuario, zm_servers.senha FROM cameras JOIN zm_servers ON cameras.zm_servers_id = zm_servers.id JOIN instituicoes ON cameras.instituicoes_id = instituicoes.id WHERE cameras.zm_id_monitor = {id_camera};")

            row = cursor.fetchone()
            if row:
                columns = [desc[0] for desc in cursor.description]
                data = dict(zip(columns, row))
                return data
            else:
                return None

        except mysql.connector.Error as err:
            print(f"Erro ao conectar ou executar a consulta: {err}")
            return None

        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals() and conn.is_connected():
                conn.close()

    @abstractmethod
    def run(self):
        pass
