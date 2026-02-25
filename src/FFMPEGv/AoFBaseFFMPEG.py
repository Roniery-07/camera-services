from abc import ABC, abstractmethod
import multiprocessing as mp
from multiprocessing import shared_memory
import logging
import cv2
import pika
import uuid
import msgpack
import imageio.v2 as imageio
from datetime import datetime
import time
import os
import json
import numpy as np
import mysql.connector
from FFMPEGv.RTSPFrameGrabber import RTSPFrameGrabber

class AoFBaseFFMPEG(mp.Process, ABC):
    def __init__(self, config_sys, config_cam, logger_name):
        super().__init__()
        self.config_sys = config_sys
        self.config_cam = config_cam
        self.logger_name = logger_name
        self.nome = config_cam.get("nomecamera", "Camera")
        
        self.URL_STREAM = f"rtsp://10.0.5.18:8554/{self.config_cam['nomecamera']}"
        self.width = int(config_cam.get("width", 1280))
        self.height = int(config_cam.get("height", 720))
        
        # YUV Size
        self.frame_byte_size = int(self.width * self.height * 3)
        
        # 1. Allocate Shared Memory (Host)
        try:
            self.shm = shared_memory.SharedMemory(create=True, size=self.frame_byte_size)
        except FileExistsError:
            self.shm = shared_memory.SharedMemory(name=f"psm_{self.nome}")

        # 2. Synchronization
        self.lock = mp.Lock()
        self.stop_event = mp.Event()

        # 3. Initialize Grabber (Child Process)
        self.grabber = RTSPFrameGrabber(
            rtsp_url=self.URL_STREAM,
            shm_name=self.shm.name,
            frame_shape=(self.height, self.width, 3),
            lock=self.lock,
            stop_event=self.stop_event,
            logger_name=self.logger_name,
            ffmpeg_bin=config_cam.get("ffmpeg_bin", "ffmpeg")
        )

        # Shared Metrics
        self.quadros_processados = mp.Value('i', 0)
        self.quadros_detectados = mp.Value('i', 0)

        self.logger = None
        self.rabbit_connection = None
        self.rabbit_channel = None
        self.callback_queue = None

    def stop(self):
        self.stop_event.set()
        self.grabber.join()
        try:
            self.shm.close()
            self.shm.unlink()
        except:
            pass

    def get_frame(self):
        """Zero-copy read from Shared Memory."""
        with self.lock:
            # Read directly from the memory buffer into a numpy array, then copy it ONCE
            # to release the lock immediately.
            try:
                frame = np.ndarray(
                    (self.height, self.width, 3), 
                    dtype=np.uint8, 
                    buffer=self.shm.buf[:self.frame_byte_size]
                ).copy()
            except Exception as e:
                if self.logger: self.logger.error(f"Memory read error: {e}")
                return None, None, None

        # Check if the frame is completely empty (camera disconnected/stalled)
        if not frame.any():
            self.logger.error("Frame is none")
            return None, None, None

        # We return the raw frame array. We no longer need frame_bytes.
        return frame, None, None

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

    # --- RabbitMQ Logic ---
    def _connect_rabbit(self):
        retries = 5
        for i in range(retries):
            try:
                self.logger.warning(f"Connecting to RabbitMQ (Attempt {i+1}/{retries})...")
                credentials = pika.PlainCredentials(
                    self.config_sys["rabbitmq"]["user"], 
                    self.config_sys["rabbitmq"]["password"]
                )
                parameters = pika.ConnectionParameters(
                    host=self.config_sys["rabbitmq"]["host"],
                    port=self.config_sys["rabbitmq"]["port"],
                    virtual_host=self.config_sys["rabbitmq"]["virtual_host"],
                    credentials=credentials,
                    heartbeat=60
                )
                self.rabbit_connection = pika.BlockingConnection(parameters)
                self.rabbit_channel = self.rabbit_connection.channel()
                
                result = self.rabbit_channel.queue_declare(queue="", exclusive=True)
                self.callback_queue = result.method.queue
                
                self.logger.info(f"RabbitMQ Connected on {self.callback_queue}")
                return True # Success
            except Exception as e:
                self.logger.error(f"Rabbit Connection Attempt {i+1} failed: {e}")
                time.sleep(2) # Wait before retrying
                
        return False # Failed all attempts
    def _ensure_connection(self):
        if self.rabbit_connection is None or self.rabbit_connection.is_closed:
            self._connect_rabbit()

    # Função para processamento distribuído via RabbitMQ
    def send_frame_to_rabbit(self, frame, camera):
        self.logger.info("sending frame")
        self._ensure_connection()

        if self.rabbit_channel is None: 
            if self.logger: self.logger.error("Connection does not exists")
            return None
        
        if not isinstance(frame, np.ndarray):
            if self.logger: self.logger.error(f"Encoding Error: Expected numpy array, got {type(frame)}. Fix CameraFixaFFMPEG.")
            return None

        try:
            ret, encoded_img = cv2.imencode('.jpg', frame)
            self.logger.info("Encoding Image")
            if not ret:
                if self.logger: self.logger.error("Failed to encode frame to JPEG")
                return None
            image_payload = encoded_img.tobytes()
        except Exception as e:
            if self.logger: self.logger.error(f"Encoding Error: {e}")
            return None

        corr_id = str(uuid.uuid4())
        response = None
        
        def on_response(ch, method, props, body):
            nonlocal response
            if props.correlation_id == corr_id:
                try:
                    payload_decoded = msgpack.unpackb(body, raw=False)
                    if "results" in payload_decoded:
                        boxes = payload_decoded["results"].get("boxes", [])
                        scores = payload_decoded["results"].get("scores", [])
                        classes = payload_decoded["results"].get("classes", [])
                        formatted = [{"bbox": b, "score": s, "class": c} for b, s, c in zip(boxes, scores, classes)]
                        response = {"results": formatted}
                    else:
                        response = {"results": []}
                except:
                    response = {"results": []}
                finally:
                    # CRITICAL FIX: This tells start_consuming() to exit!
                    # Without this, the code freezes forever waiting for more messages.
                    ch.stop_consuming()

        try:
            self.rabbit_channel.basic_consume(
                queue=self.callback_queue, 
                on_message_callback=on_response, 
                auto_ack=True
            )

            payload = {
                "image": image_payload,
                "y_conf": camera.get("y_conf", 0.5),
                "y_iou": camera.get("y_iou", 0.45),
            }
            body = msgpack.packb(payload, use_bin_type=True)
            
            self.rabbit_channel.basic_publish(
                exchange=self.config_sys["rabbitmq"]["exchange"],
                routing_key=self.config_sys["rabbitmq"]["routing_key"],
                properties=pika.BasicProperties(
                    reply_to=self.callback_queue, 
                    correlation_id=corr_id, 
                ),
                body=body
            )

            self.rabbit_channel.start_consuming()

            return response if response is not None and 'results' in response else None

        except Exception as e:
            if self.logger: self.logger.error(f"Rabbit Publish Error: {e}")
            try:
                self.rabbit_connection.close()
            except: pass
            self.rabbit_connection = None
            return None

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
