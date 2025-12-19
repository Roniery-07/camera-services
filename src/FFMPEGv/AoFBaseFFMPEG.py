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
import subprocess as sp
import time
import re
import select

# Thread para controle FFMPEG
class RTSPFrameGrabber:
    def __init__(self, rtsp_url, frame_shape, logger, ffmpeg_bin="ffmpeg", reconnect_interval=15):
        self.rtsp_url = rtsp_url
        self.frame_shape = frame_shape  # (height, width, channels)
        self.ffmpeg_bin = ffmpeg_bin
        self.reconnect_interval = reconnect_interval
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._latest_pts = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.logger = logger

    def start(self):
        if not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def get_frame(self):
        with self._frame_lock:
            if self._latest_frame is not None:
                return self._latest_frame.copy(), self._latest_pts
            else:
                return None, None

    def _run(self):
        while True:
            ffmpeg_cmd = [
                self.ffmpeg_bin,
                "-hide_banner",
                "-loglevel", "info",
                "-threads", "1",
                "-fflags", "nobuffer",
                "-flags", "low_delay",
                "-rtsp_transport", "tcp",
                "-an",
                "-c:v", "h264_cuvid",
                "-i", self.rtsp_url,
                "-r", "2",
                "-vf", "showinfo",
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-"
            ]
            process = None
            try:
                process = sp.Popen(
                    ffmpeg_cmd,
                    stdout=sp.PIPE,
                    stderr=sp.PIPE,
                    bufsize=-1
                )
                frame_size = int(np.prod(self.frame_shape))
                latest_pts = None

                last_frame_time = time.time()
                timeout_sec = 30  # máximo tempo sem novo frame
                stagnant_frame_limit = 15  # número máximo de frames repetidos
                stagnant_frame_count = 0
                last_pts = None

                while True:
                    rlist, _, _ = select.select([process.stdout, process.stderr], [], [], 1)
                    if not rlist:
                        # Timeout: não recebeu nada
                        if time.time() - last_frame_time > timeout_sec:
                            raise RuntimeError("[FFMPEG] Timeout: não recebeu frame em tempo hábil.")
                        continue

                    if process.stdout in rlist:
                        raw_frame = process.stdout.read(frame_size)
                        if len(raw_frame) != frame_size:
                            self.logger.error("[FFMPEG] Frame size mismatch or stream ended. Expected %d bytes, got %d.", frame_size, len(raw_frame))
                            raise RuntimeError("[FFMPEG] Frame size mismatch or stream ended.")

                        frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape(self.frame_shape)
                        last_frame_time = time.time()

                        # Atualiza frame + PTS juntos
                        with self._frame_lock:
                            self._latest_frame = frame
                            self._latest_pts = latest_pts

                        # Detecta frame/PTS estagnado
                        if latest_pts == last_pts:
                            stagnant_frame_count += 1
                            if stagnant_frame_count > stagnant_frame_limit:
                                raise RuntimeError("[FFMPEG] Frame/PTS estagnado. Reiniciando FFmpeg.")
                        else:
                            stagnant_frame_count = 0
                            last_pts = latest_pts

                    if process.stderr in rlist:
                        line = process.stderr.readline().decode("utf-8", errors="ignore")
                        if "pts_time:" in line:
                            m = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
                            if m:
                                latest_pts = float(m.group(1))

            except Exception as e:
                sleep_time = self.reconnect_interval
                self.logger.error(f"[FFMPEG] {e}, reconnecting in {sleep_time}s)")
                time.sleep(sleep_time)
            finally:
                if process is not None:
                    try:
                        process.kill()
                    except Exception as e:
                        self.logger.warning(f"Error killing FFMPEG: {e}")
                    try:
                        process.wait(timeout=5)
                    except Exception as e:
                        self.logger.warning(f"Error waiting for FFMPEG termination: {e}")
                    try:
                        if process.stdout:
                            process.stdout.close()
                        if process.stderr:
                            process.stderr.close()
                    except Exception as e:
                        self.logger.warning(f"Error closing FFMPEG pipes: {e}")

# Classe base para todos os tipos de câmeras
class AoFBaseFFMPEG(threading.Thread, ABC):
    def __init__(self, config_sys, config_cam, nome="Camera"):
        super().__init__()  # Nome da thread padrão: Thread-1, Thread-2, etc.
        self.nome = nome
        self.config_sys = config_sys
        self.config_cam = config_cam
        # Definição de Logs
        self.logger = configurar_logger(nome)
        # Configura a classe para FFMPEG externo
        #self.URL_STREAM = f"rtsp://10.0.5.246:2000/{self.config_cam['nomecamera']}?username=admin&password=4jAMH@h*W6@$W3K"
        self.URL_STREAM = f"rtsp://10.0.5.18:8554/{self.config_cam['nomecamera']}"
        self.frame_shape = (
            int(config_cam.get("height", 720)),
            int(config_cam.get("width", 1280)),
            int(config_cam.get("channels", 3))
        )
        self.grabber = RTSPFrameGrabber(
            rtsp_url = self.URL_STREAM,
            frame_shape = self.frame_shape,
            logger = self.logger,
            ffmpeg_bin=config_cam.get("ffmpeg_bin", "ffmpeg"),
            reconnect_interval=int(config_cam.get("reconnect_interval", 15))
        )
        # Contabilidade de Métricas com Zabbix
        self.quadros_processados = 0
        self.quadros_detectados = 0
        self.lock = threading.Lock()

    # Inicia a thread do grabber junto com a thread da classe base
    def start(self):
        self.grabber.start()
        super().start()

    @abstractmethod
    def run(self):
        pass

    # Função para obter uma imagem JPEG da thread da classe RTSPFrameGrabber
    def get_frame(self):
        try:
            frame, pts = self.grabber.get_frame()
            if frame is None:
                return None, None, None

            result, encoded_img = cv2.imencode('.jpg', frame)
            if not result:
                return None, None, None

            return frame, encoded_img.tobytes(), pts
 
        except Exception as e:
            self.logger.error(f"Erro ao obter frame: {e}.")
            return None, None, None

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
            print(f"Erro ao conectar ou executar a inserção: {err}")
            return None

        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals() and conn.is_connected():
                conn.close()

    # Função para salvar a um arquivo de vídeo e seus metadados
    def save_all_analyzed_video(self, metadados, analyzed_frames, analyzed_frames_wbbox):
        camera_name = self.nome
        output_dir = self.config_sys["locais"]["diretorio_videos"] + "/"
        os.makedirs(output_dir, exist_ok=True)
        FPS = self.config_sys["detector"]["max_fps_processamento"]
        detection_count = metadados["detection_count"]
        width = self.config_cam["width"]
        height = self.config_cam["height"]

        if len(analyzed_frames) == 0 or len(analyzed_frames_wbbox) == 0:
            self.logger.error(f"Variáveis de frames zeradas! Não foi possível gerar MP4.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_filename = f"{camera_name}_det{detection_count}_{timestamp}"
        video_filename = f"{base_filename}.mp4"
        metadados["video_filename"] = video_filename
        video_filepath = os.path.join(output_dir, video_filename)

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

        video_filename = f"{base_filename}_wbbox.mp4"
        metadados["video_filename_wbbox"] = video_filename
        video_filepath = os.path.join(output_dir, video_filename)
        writer = imageio.get_writer(video_filepath, fps=FPS, codec="libx264", format='ffmpeg', pixelformat='yuv420p', ffmpeg_log_level="error", output_params=["-profile:v", "baseline", "-level", "3.0", "-movflags", "+faststart", "-an"])
        for frame in analyzed_frames_wbbox:
            # Se estiver em BGR (OpenCV), converta para RGB
            if frame.shape[2] == 3:
                frame = frame[:, :, ::-1]
            writer.append_data(frame)
        writer.close()
        self.logger.warning(f"Vídeo com BBox salvo em: {video_filepath}")

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

    # Função para o desenho das bboxes no frame
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

    def ajustar_ptz(self, bbox, ptz_atual, largura=1280, altura=720, fov_h=61.8, fov_v=37.1, proporcao_alvo=0.5, zoom_maximo=0.6, zoom_minimo=0.0 ):
        # FOVs reais Bosch Autodome 5100i IR
        fov_v_min = fov_v      # 37.1 graus (wide)
        fov_v_max = 1.3        # 1.3 graus (tele)
        fov_h_min = fov_h      # 61.8 graus (wide)
        fov_h_max = 2.3        # 2.3 graus (tele)
        PAN_RANGE_GRAUS = 180.0
        TILT_RANGE_GRAUS = 90.0

        x_min, y_min, x_max, y_max = bbox
        pan_atual, tilt_atual, zoom_atual = ptz_atual

        # Cambalacho dinâmico feito no zoom_máximo, para aumentar de acordo com o zoom_atual
        # algo que aparentemente ajuda na centralização e foco da imagem...
        # Obs.: não tenho explicação pra isso...
        #zoom_maximo = min(zoom_maximo + (zoom_atual ** 1.7) * 0.7, 1.0)
        zoom_maximo = min(zoom_atual + 0.4, 1.0)

        # Interpolação calibrada do FOV
        def interpolar_fov(zoom, zoom_min, zoom_max, fov_min, fov_max):
            if zoom_max == zoom_min:
                return fov_min
            t = (zoom - zoom_min) / (zoom_max - zoom_min)
            return fov_min + t * (fov_max - fov_min)

        fov_v_atual = interpolar_fov(zoom_atual, zoom_minimo, zoom_maximo, fov_v_min, fov_v_max)
        fov_h_atual = interpolar_fov(zoom_atual, zoom_minimo, zoom_maximo, fov_h_min, fov_h_max)

        # Fator de correção para zoom
        fator_correcao = fov_h_atual / fov_h_min

        # Cálculo do centro da bbox e do frame (float)
        x_bb = (x_min + x_max) / 2.0
        y_bb = (y_min + y_max) / 2.0
        x_centro = largura / 2.0
        y_centro = altura / 2.0

        # Cada pixel equivale a quantos graus no FOV atual
        graus_por_px_h = (fov_h_atual / largura) * fator_correcao
        graus_por_px_v = (fov_v_atual / altura) * fator_correcao

        delta_pan_graus = (x_bb - x_centro) * graus_por_px_h
        delta_tilt_graus = -(y_bb - y_centro) * graus_por_px_v  # Sinal negativo para coordenada de imagem

        # Normalização
        delta_pan_norm = delta_pan_graus / PAN_RANGE_GRAUS
        delta_tilt_norm = delta_tilt_graus / TILT_RANGE_GRAUS

 
        # Remendo no código para inverter para valor negativo quando pan ou til >1
        def wrap_circular(valor):
            # Mapeia qualquer valor para o intervalo [-1.0, 1.0)
            return ((valor + 1.0) % 2.0) - 1.0

        pan_novo = wrap_circular(pan_atual + delta_pan_norm)
        tilt_novo = wrap_circular(tilt_atual + delta_tilt_norm)

#        pan_novo = pan_atual + delta_pan_norm
#        tilt_novo = tilt_atual + delta_tilt_norm
 
        # Limitando entre -1 e 1
        pan_novo = max(min(pan_novo, 1.0), -1.0)
        tilt_novo = max(min(tilt_novo, 1.0), -1.0)

        # Zoom proporcional com margem (inalterado)
        bb_width = x_max - x_min
        bb_height = y_max - y_min
        prop_w = bb_width / largura
        prop_h = bb_height / altura
        prop_maior = max(prop_w, prop_h)

        if prop_maior >= proporcao_alvo:
            zoom_novo = zoom_atual
        else:
            ganho = (proporcao_alvo - prop_maior) / proporcao_alvo
            zoom_novo = zoom_atual + ganho * (zoom_maximo - zoom_atual)

        zoom_novo = max(min(zoom_novo, zoom_maximo), zoom_minimo)

        return round(pan_novo, 3), round(tilt_novo, 3), round(zoom_novo, 3)
