from abc import ABC, abstractmethod
import threading
from Utilidades import configurar_logger
import cv2
import pika
import uuid
import msgpack
import imageio.v2 as imageio
from datetime import datetime
import os
import json
import requests
import numpy as np
import mysql.connector

# Classe base para todos os tipos de câmeras
class AoFBase(threading.Thread, ABC):
    def __init__(self, config_sys, config_cam, nome="Camera"):
        super().__init__()  # Nome da thread padrão: Thread-1, Thread-2, etc.
        self.nome = nome
        self.config_sys = config_sys
        self.config_cam = config_cam
        # Configura as variáveis de URL e Timeout para obtenção do Snapshot para processamento
        # O https foi removido pois essas conexões geralmente serão locais, sem o SSL as múltiplas conexões são mais leves
        self.URL_SNAPSHOT = f"http://{self.config_cam['url']}/zm/cgi-bin/nph-zms?mode=single&monitor={self.config_cam['zm_id_monitor']}&user={self.config_cam['usuario']}&pass={self.config_cam['senha']}"
        self.GET_SNAPSHOT_TIMEOUT = int(config_sys["detector"]["max_fps_processamento"]) / 4
        # Definição de Logs
        self.logger = configurar_logger(nome)
        # Contabilidade de Métricas com Zabbix
        self.quadros_processados = 0
        self.quadros_detectados = 0
        self.lock = threading.Lock()

    @abstractmethod
    def run(self):
        pass

    # Função para obter uma imagem JPEG do ZoneMinder
    # Retorna um array np e a imagem em bytes, para evitar reversão ao enviar para processamento distribuído
    def get_frame(self):
        try:
            response = requests.get(self.URL_SNAPSHOT, self.GET_SNAPSHOT_TIMEOUT)
            response.raise_for_status()

            img_array = np.frombuffer(response.content, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

            if frame is None:
                return None, None
            return frame, response.content

        except Exception as e:
            self.logger.error(f"Erro ao obter frame: {e}.")
            return None, None

    # Funçao para o fornecimento das métricas via objeto thread
    def zbx_metrics(self):
        with self.lock:
            return self.quadros_processados, self.quadros_detectados

    # Função para inserção dos dados dos processamentos realizados por câmera e coordenada
    def insere_dados_processamentos(self, config_sys, config_cam, dados):
        db = config_sys["database"]

        try:
            conn = mysql.connector.connect(
                host=db["host"],
                user=db["user"],
                password=db["password"],
                database=db["database"]
            )

            cursor = conn.cursor()
            query = """
                INSERT INTO processamentos (coordenadas_cameras_id, cameras_id, datahora, qt_analyzed_frames, qt_analyzed_frames_busca, qt_analyzed_frames_confirmacao, qt_detection_count, qt_detection_count_busca, qt_detection_count_confirmacao)
                VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, %s)
            """
            valores = (dados[0], config_cam["idcamera"], dados[1], dados[2], dados[3], dados[4], dados[5], dados[6])
            cursor.execute(query, valores)
            conn.commit()
            # Retorna o ID do registro inserido
            return cursor.lastrowid

        except mysql.connector.Error as err:
            self.logger.error(f"Erro ao conectar ou executar a inserção: {err}")
            return None

        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals() and conn.is_connected():
                conn.close()

    # Função para inserção das detecções realizadas por câmera e coordenada
    def insere_dados_deteccoes(self, mp4, jpeg, metadados):
        db = self.config_sys["database"]

        try:
            conn = mysql.connector.connect(
                host=db["host"],
                user=db["user"],
                password=db["password"],
                database=db["database"]
            )

            cursor = conn.cursor()
            query = """
                INSERT IGNORE INTO deteccoes (processamentos_id, datahora, mp4, thumbnail, metadados) VALUES (%s, %s, %s, %s, %s)
            """

            processamentos_id = metadados["processamentos_id"]
            dt = datetime.fromtimestamp(metadados["timestamp"])
            mysql_datetime = dt.strftime('%Y-%m-%d %H:%M:%S')
            values = (processamentos_id, mysql_datetime, mp4, jpeg, json.dumps(metadados))
            cursor.execute(query, values)
            conn.commit()

            # Retorna o ID do registro inserido
            return cursor.lastrowid

        except mysql.connector.Error as err:
            self.logger.error(f"Erro ao conectar ou executar a inserção: {err}")
            return None

        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals() and conn.is_connected():
                conn.close()

    # Função para salvar a um arquivo de vídeo e seus metadados
    def save_all_analyzed_video(self, metadados, analyzed_frames):
        camera_name = self.nome
        output_dir = self.config_sys["locais"]["diretorio_videos"] + "/"
        os.makedirs(output_dir, exist_ok=True)
        FPS = self.config_sys["detector"]["max_fps_processamento"]
        detection_count = metadados["detection_count"]
        width = self.config_cam["width"]
        height = self.config_cam["height"]

        if len(analyzed_frames) == 0:
            self.logger.error(f"Variáveis de frames zeradas! Não foi possível gerar MP4.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_filename = f"{camera_name}_det{detection_count}_{timestamp}"
        video_filename = f"{base_filename}.mp4"
        metadados["video_filename"] = video_filename
        video_filepath = os.path.join(output_dir, camera_name, video_filename)

        # A utilização do imageio ao invés do cv2.VideoWriter é em decorrência da falta de suporte a mp4 do segundo
        # Eu poderia compilar o OpenCV com suporte a MP4, mas não estou a fim, pip install imageio[ffmpeg] foi muito mais fácil 
        writer = imageio.get_writer(video_filepath, fps=FPS, codec="libx264", format='ffmpeg', pixelformat='yuv420p', ffmpeg_log_level="error", output_params=["-profile:v", "baseline", "-level", "3.0", "-movflags", "+faststart", "-an"])
        for frame in analyzed_frames:
            # Se estiver em BGR (OpenCV), converta para RGB
            if frame.shape[2] == 3:
                frame = frame[:, :, ::-1]
            writer.append_data(frame)
        writer.close()
        self.logger.warning(f"Vídeo sem BBox salvo em: {video_filepath}")

        # Salva os metadados em um arquivo .txt (sem os frames, para não ficar gigante)
        meta_filename = f"{base_filename}_meta.txt"
        metadados["meta_filename"] = meta_filename
        meta_filepath = os.path.join(output_dir, meta_filename)
        metadados_to_save = {k: v for k, v in metadados.items()}
        try:
            with open(meta_filepath, "w", encoding="utf-8") as meta_file:
                json.dump(metadados_to_save, meta_file, indent=4, ensure_ascii=False)
            self.logger.warning(f"Metadados salvos em: {meta_filepath}")
        except Exception as e:
            self.logger.error(f"Erro ao salvar metadados: {e}")

        # Gravando thumbnail
        maior_score = max(metadados["todas_bboxes"], key=lambda x: x["score"])
        frame_idx_maior_score = int(maior_score["frame_idx"])
        bbox_idx_maior_score = maior_score["bbox"]
        class_idx_maior_score = maior_score["class"]
        score_idx_maior_score = maior_score["score"]
        frame_maior_score = analyzed_frames[frame_idx_maior_score]
        # Gravar bbox no frame de maior score
        frame_maior_score_bbox = self.draw_box_fbcs(frame_maior_score, bbox_idx_maior_score, class_idx_maior_score, score_idx_maior_score)
        thumbnail_filename = f"{base_filename}.jpg"
        thumbnail_filepath = os.path.join(output_dir, camera_name, thumbnail_filename)
        cv2.imwrite(thumbnail_filepath, frame_maior_score_bbox, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        self.logger.warning(f"Thumbnail salvo em: {thumbnail_filepath}")

        # Gravando a detecção no banco de dados
        deteccaoId = self.insere_dados_deteccoes(os.path.join(camera_name, video_filename), os.path.join(camera_name, thumbnail_filename), metadados)
        self.logger.warning(f"Detecção gravada no banco e dados com ID {deteccaoId}")

    # Função para o desenho das bboxes no frame com results do Yolo
    def draw_boxes(self, image, results):
        CLASSES = self.config_sys["detector"]["CLASSES"]
        COLORS = self.config_sys["detector"]["COLORS"]
        h, w = image.shape[:2]
        pad = 5  # padding para texto
        for box in results:
            cls_id = int(box['class'])
            conf = float(box['score'])
            x1, y1, x2, y2 = map(int, box['bbox'])
            color = COLORS[cls_id % len(COLORS)]
            label = f"{CLASSES[cls_id]} ({int(conf*100)}%)"
            ((text_w, text_h), _) = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            y_text = max(y1 - 10, text_h + pad)
            if y1 - text_h - pad < 0:
                y_text = min(y1 + text_h + pad, h - pad)
            x_text = min(max(x1, pad), w - text_w - pad)
            cv2.rectangle(image, (x_text - pad, y_text - text_h - pad), (x_text + text_w + pad, y_text + pad), color, -1)
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
            cv2.putText(image, label, (x_text, y_text), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        return image

    # Função para o desenho das bboxes no frame com bbox e class_id
    def draw_box_fbcs(self, frame, bbox, class_id, score):
        CLASSES = self.config_sys["detector"]["CLASSES"]
        COLORS = self.config_sys["detector"]["COLORS"]
        h, w = frame.shape[:2]
        pad = 5  # padding para texto
        x1, y1, x2, y2 = map(int, bbox)
        color = COLORS[class_id % len(COLORS)]

        # Monta label (com ou sem score)
        if score is not None:
            label = f"{CLASSES[class_id]} ({int(score*100)}%)"
        else:
            label = f"{CLASSES[class_id]}"

        ((text_w, text_h), _) = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        y_text = max(y1 - 10, text_h + pad)
        if y1 - text_h - pad < 0:
            y_text = min(y1 + text_h + pad, h - pad)
        x_text = min(max(x1, pad), w - text_w - pad)
        cv2.rectangle(frame,(x_text - pad, y_text - text_h - pad),(x_text + text_w + pad, y_text + pad),color, -1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, label, (x_text, y_text),cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        return frame

    # Função para processamento distribuído via RabbitMQ
    def send_frame_to_rabbit(self, frame_bytes, camera):
        credentials = pika.PlainCredentials(self.config_sys["rabbitmq"]["user"], self.config_sys["rabbitmq"]["password"])
        parameters = pika.ConnectionParameters(
            host = self.config_sys["rabbitmq"]["host"],
            port = self.config_sys["rabbitmq"]["port"],
            virtual_host = self.config_sys["rabbitmq"]["virtual_host"],
            credentials = credentials
        )

        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        result = channel.queue_declare(queue='', exclusive=True)
        callback_queue = result.method.queue

        corr_id = str(uuid.uuid4())

        payload = {
            'image': frame_bytes,
            'y_conf': camera['y_conf'],
            'y_iou': camera['y_iou']
        }
        body = msgpack.packb(payload, use_bin_type=True)
        response = {}

        def on_response(ch, method, props, body):
            if props.correlation_id == corr_id:
                payload = msgpack.unpackb(body, raw=False)

                boxes = payload['results']['boxes']
                scores = payload['results']['scores']
                classes = payload['results']['classes']

                results = [
                    {
                        'bbox': bbox,
                        'score': score,
                        'class': cls
                    } for bbox, score, cls in zip(boxes, scores, classes)
                ]
                response['results'] = results
                ch.stop_consuming()

        channel.basic_consume(queue=callback_queue, on_message_callback=on_response, auto_ack=True)
        channel.basic_publish(
            exchange = self.config_sys["rabbitmq"]["exchange"],
            routing_key = self.config_sys["rabbitmq"]["routing_key"],
            properties=pika.BasicProperties(
                reply_to=callback_queue,
                correlation_id=corr_id
            ),
            body=body
        )

        channel.start_consuming()
        connection.close()
        return response if 'results' in response else None

    # Retorna todas as BBox em formato lista considerando a possibilidade de mais de uma por frame
    # Calcula a BBox média em pixel e em percentual em relação à dimensão da imagem
    def processar_bboxes(self, todos_results):
        img_width = self.config_cam["width"]
        img_height = self.config_cam["height"]
        todas_bboxes = []
        for idx_frame, results in enumerate(todos_results):
            for box in results:
                bbox = list(map(int, box['bbox']))  # [x1, y1, x2, y2]
                bbox_info = {
                    'frame_idx': idx_frame,
                    'class': int(box['class']),
                    'score': float(box['score']),
                    'bbox': bbox
                }
                todas_bboxes.append(bbox_info)

        if not todas_bboxes:
            return [], None, None

        # Calcular médias
        x1s = [bbox['bbox'][0] for bbox in todas_bboxes]
        y1s = [bbox['bbox'][1] for bbox in todas_bboxes]
        x2s = [bbox['bbox'][2] for bbox in todas_bboxes]
        y2s = [bbox['bbox'][3] for bbox in todas_bboxes]

        bbox_media_pixels = [
            int(sum(x1s) / len(x1s)),
            int(sum(y1s) / len(y1s)),
            int(sum(x2s) / len(x2s)),
            int(sum(y2s) / len(y2s)),
        ]

        # Percentual
        bbox_media_percentual = [
            bbox_media_pixels[0] / img_width,
            bbox_media_pixels[1] / img_height,
            bbox_media_pixels[2] / img_width,
            bbox_media_pixels[3] / img_height,
        ]

        # Percentual da área da bbox média em relação à área da imagem
        largura_bbox = bbox_media_pixels[2] - bbox_media_pixels[0]
        altura_bbox = bbox_media_pixels[3] - bbox_media_pixels[1]
        area_bbox_media = max(0, largura_bbox) * max(0, altura_bbox)
        area_img = img_width * img_height
        percentual_area_bbox_media = area_bbox_media / area_img

        return todas_bboxes, bbox_media_pixels, bbox_media_percentual, percentual_area_bbox_media

    # Retorna o bbox com maior valor de score
    def bbox_maior_score(self, todos_results):
        melhor_bbox = None
        maior_score = float('-inf')
        for idx_frame, results in enumerate(todos_results):
            for box in results:
                score = float(box['score'])
                if score > maior_score:
                    maior_score = score
                    melhor_bbox = {
                        'frame_idx': idx_frame,
                        'class': int(box['class']),
                        'score': score,
                        'bbox': list(map(int, box['bbox']))
                    }
        return melhor_bbox
