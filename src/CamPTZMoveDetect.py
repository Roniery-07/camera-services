from AoFBase import AoFBase
from CamBaseFixa import CamBaseFixa
import mysql.connector
import json
import time
import cv2

# Subclasse de Câmeras Fixas
class CamPTZMoveDetect(CamBaseFixa):
    def camera_em_movimento(self, frame_atual, frame_anterior, resize_dim=(320, 240), limiar_movimento=20, frames_para_parado=3):
        # Verificação básica de resiliência
        if frame_atual is None or frame_anterior is None:
            self.contador_parado = 0
            return True  # Assume movimento se frames inválidos

        try:
            # Redimensiona e converte para cinza
            atual_gray = cv2.cvtColor(cv2.resize(frame_atual, resize_dim), cv2.COLOR_BGR2GRAY)
            anterior_gray = cv2.cvtColor(cv2.resize(frame_anterior, resize_dim), cv2.COLOR_BGR2GRAY)

            # Diferença absoluta e média
            diff = cv2.absdiff(atual_gray, anterior_gray)
            media_diff = diff.mean()

            if media_diff > limiar_movimento:
                # movimento detectado → reseta contador
                self.contador_parado = 0
                return True
            else:
                # sem movimento → incrementa contador
                self.contador_parado += 1
                if self.contador_parado >= frames_para_parado:
                    return False  # considerado realmente parado
                else:
                    return True  # ainda considera em movimento até atingir frames_para_parado

        except Exception as e:
            # Loga o erro e assume movimento
            self.logger.info(f"Erro ao processar frames para detecção de movimento: {e}")
            self.contador_parado = 0
            return True

    def run(self):
        self.logger.info("Thread Câmera PTZ com Detecção de Movimento iniciada.")

        # Obtém informações da câmera
        camera = self.atualiza_dados_camera_fixa(self.config_sys, self.config_cam)
        duracaobusca = camera["duracaobusca"]
        duracaoconfirmacao = camera["duracaoconfirmacao"]

        # Temporizador para o processos de busca
        deadline = time.monotonic() + duracaobusca + duracaoconfirmacao
        emconfirmacao = False

        # Limitação de FPS
        frames_por_segundo = 1.0 / float(self.config_sys["detector"]["max_fps_processamento"])
        timestamp_ultimo_frame = time.time()

        # Contagem e armazenamento de frames
        detection_count = 0
        detection_count_confirmacao = 0
        analyzed_frames_confirmacao = 0 
        analyzed_frames = []

        # Variável que armazena todos os results obtidos
        todos_results = []

        # Variável que indica movimento no PTZ
        PTZEmMovimento = False
        prev = None

        while True:
            # Obtendo frames (snapshot) por meio do método self.get_frame()
            frame, frame_bytes = self.get_frame()       
            if frame is None:
                time.sleep(0.1)
                continue

            # Limitação de FPS
            tempo_agora = time.time()
            tempo_desde_ultimo_frame = tempo_agora - timestamp_ultimo_frame
            tempo_espera = frames_por_segundo - tempo_desde_ultimo_frame
            if tempo_espera > 0:
                time.sleep(tempo_espera)
            timestamp_ultimo_frame = time.time()

            # Verifica se a câmera está parada
            if self.camera_em_movimento(frame, prev):
                prev = frame
                PTZEmMovimento = True
            else:
                prev = frame
                if emconfirmacao == False:
                    self.logger.info(f"O PTZ parou! Início do período de busca.")
                PTZEmMovimento = False
                emconfirmacao = True

                # Envia frame para processamento por meio do RabbitMQ (processamento distribuído)
                response = self.send_frame_to_rabbit(frame_bytes, camera)
                results = response['results']
                todos_results.append(results)

                # Contabiliza e notifica imeditamente em caso de detecção no frame
                if len(results) > 0:
                    self.logger.warning(f"Detectado {self.config_sys['detector']['CLASSES'][int(results[0]['class'])]} com Score {float(results[0]['score']):.3f} na BBox {list(map(int, results[0]['bbox']))}.")
                    detection_count += 1
                    detection_count_confirmacao += 1
                analyzed_frames_confirmacao += 1

                # Reservando Frames sem BBox e com BBox
                analyzed_frames.append(frame.copy())

            # Controle dos temporizadores
            agora = time.monotonic()

            # Controla o tempo de busca pelo tempo ou movimentação do PTZ
            if agora < deadline and PTZEmMovimento == False:
                continue
            elif PTZEmMovimento == True and emconfirmacao == False:
                continue

            # Fim do preíodo de busca ou confirmação na coordenada
            if PTZEmMovimento == True:
                self.logger.info(f"O PTZ movimentou! Final da busca: {len(analyzed_frames)} Frames Analisados com {detection_count} Detecções.")
            else:
                self.logger.info(f"Limite de tempo! Final da busca: {len(analyzed_frames)} Frames Analisados com {detection_count} Detecções.")

            if detection_count > 0:
                # BBox de maior score
                bbox_maior_score = self.bbox_maior_score(todos_results)
                bbox_maior_score = list(map(int, bbox_maior_score["bbox"]))

            # Contabilizando dados do processamento
            qt_analyzed_frames = len(analyzed_frames)
            qt_analyzed_frames_busca = qt_analyzed_frames-analyzed_frames_confirmacao
            qt_detection_count_busca = detection_count-detection_count_confirmacao

            # Salva informações de processamento para os períodos que tiveram confirmação
            if emconfirmacao and detection_count > 0:
                dados = [None, qt_analyzed_frames, qt_analyzed_frames_busca, analyzed_frames_confirmacao, detection_count, qt_detection_count_busca, detection_count_confirmacao]
                # Envia para o banco de dados
                processamentos_id = self.insere_dados_processamentos(self.config_sys, self.config_cam, dados)

            # Verificando o threshold para geração de arquivo MP4
            if (detection_count/qt_analyzed_frames) >= camera["perc_frames_para_mp4"] and qt_analyzed_frames > 8:
                # Construção dos metadados
                todas_bboxes, bbox_media_pixels, bbox_media_percentual, percentual_area_bbox_media = self.processar_bboxes(todos_results)
                metadados = {}
                metadados["timestamp"] = int(time.time())
                metadados["idcamera"] = self.config_cam["idcamera"]
                metadados["processamentos_id"] = processamentos_id
                metadados["zm_id_monitor"] = self.config_cam["zm_id_monitor"]
                metadados["nomecamera"] = self.config_cam["nomecamera"]
                metadados["chat_id_telegram"] = self.config_cam["chat_id_telegram"]
                metadados["analyzed_frames"] = qt_analyzed_frames
                metadados["analyzed_frames_busca"] = qt_analyzed_frames_busca
                metadados["analyzed_frames_confirmacao"] = analyzed_frames_confirmacao
                metadados["detection_count"] = detection_count
                metadados["detection_count_busca"] = qt_detection_count_busca
                metadados["detection_count_confirmacao"] = detection_count_confirmacao
                metadados["y_conf"] = self.config_cam["y_conf"]
                metadados["y_iou"] = self.config_cam["y_iou"]
                metadados["perc_frames_para_mp4"] = self.config_cam["perc_frames_para_mp4"]
                metadados["fps"] = self.config_cam["fps"]
                metadados["width"] = self.config_cam["width"]
                metadados["height"] = self.config_cam["height"]
                metadados["percentual_area_bbox_media"] = percentual_area_bbox_media
                metadados["bbox_maior_score"] = bbox_maior_score
                metadados["bbox_media_percentual"] = bbox_media_percentual
                metadados["bbox_media_pixels"] = bbox_media_pixels
                metadados["todas_bboxes"] = todas_bboxes
                # Salva arquivos no disco
                self.save_all_analyzed_video(metadados, analyzed_frames)

            # Atualiza métricas para o Zabbix
            with self.lock:
                self.quadros_processados +=  len(analyzed_frames)
                self.quadros_detectados += detection_count
            # Resetando variáveis por falta de detecção (novo ciclo de busca)
            analyzed_frames.clear()
            todos_results = []
            detection_count = 0
            # Resetando contadores de frames confirmados no início do novo período
            detection_count_confirmacao = 0
            analyzed_frames_confirmacao = 0
            # Atualizando dados da câmera
            camera = self.atualiza_dados_camera_fixa(self.config_sys, self.config_cam)
            duracaobusca = camera["duracaobusca"]
            duracaoconfirmacao = camera["duracaoconfirmacao"]

            # Restabelecendo o controle de tempo
            agora = time.monotonic()
            deadline = agora + duracaobusca + duracaoconfirmacao
            emconfirmacao = False
