from FFMPEGv.AoFBaseFFMPEG import AoFBaseFFMPEG
from FFMPEGv.CamBasePTZFFMPEG import CamBasePTZFFMPEG
import json
import time
from sensecam_control import onvif_control

#
# Subclasse de Câmeras PTZ Bosch
#
class CameraPTZMBoschFFMPEG(CamBasePTZFFMPEG):
    def conectar_ptz_bosch(self, camera):
        ptz = onvif_control.CameraControl(camera["ptz_ip"], camera["ptz_user"], camera["ptz_password"])
        ptz.camera_start()
        return ptz

    def run(self):
        self.logger.info("Thread Câmera PTZBosch iniciada.")

        # Obtém informações da câmera
        camera = self.atualiza_dados_camera_ptz(self.config_sys, self.config_cam)
        duracaobusca = camera["duracaobusca"]
        duracaoconfirmacao = camera["duracaoconfirmacao"]

        # Temporizador para o processos de busca
        deadline = time.monotonic() + duracaobusca
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

        # Controla o vetor de coordenadas da câmera
        idx_coord = 0
        coordenadas = camera["coordenadas"]

        # Conecta ao controle de PTZ
        ptzcam = self.conectar_ptz_bosch(camera)

        # Inicia período de busca
        if camera["ptz_ctrl_ativo"] == 1:
            p, t, z = coordenadas[idx_coord][1:4]
            ptzcam.absolute_move( p, t, z )
            self.logger.info(f"Início do período de busca na coordenada {tuple(coordenadas[idx_coord][1:4])}.")
            time.sleep( self.config_cam["ptz_delay_stream"] )
        else:
            self.logger.warning(f"Controle ativo de PTZ desligado, iniciando o período de busca sem atuação no PTZ.")

        while True:
            # Obtendo frames (snapshot) por meio do método self.get_frame()
            frame, frame_bytes, pts = self.get_frame() 
            if frame is None:
                time.sleep(0.1)
                continue

            print(f"[FFMPEG] PTS extraído: {pts}")

            # Limitação de FPS
            tempo_agora = time.time()
            tempo_desde_ultimo_frame = tempo_agora - timestamp_ultimo_frame
            tempo_espera = frames_por_segundo - tempo_desde_ultimo_frame
            if tempo_espera > 0:
                time.sleep(tempo_espera)
            timestamp_ultimo_frame = time.time()

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

            # Controla a dilação de tempo
            if agora < deadline:
                continue
                #
            elif detection_count > 0 and not emconfirmacao:
                # BBox de maior score
                bbox_maior_score = self.bbox_maior_score(todos_results)
                bbox_maior_score = list(map(int, bbox_maior_score["bbox"]))
                # Ajustar o PTZ na BBox se o controle estiver ativo
                if camera["ptz_ctrl_ativo"] == 1:
                    # Obtendo o PTZ atual e calculado o novo para a melhor BBox
                    ptz_atual = ptzcam.get_ptz()
                    panA, tiltA, zoomA = ptz_atual
                    p, t, z = self.ajustar_ptz(bbox_maior_score, ptz_atual)
                    #
                    self.logger.warning(f"Ajustando o PTZ para confirmação em função do BBox {bbox_maior_score} de ({panA:.3f}, {tiltA:.3f}, {zoomA:.3f}) --> ({p:.3f}, {t:.3f}, {z:.3f}).")
                    ptzcam.absolute_move(p, t, z)
                    time.sleep( self.config_cam["ptz_delay_stream"] )
                else:
                    self.logger.warning(f"Controle ativo de PTZ desligado, iniciando o período de confirmação sem atuação no PTZ.")
                # Iniciando período de confirmação
                deadline += duracaoconfirmacao
                emconfirmacao = True
                # Resetando contadores de frames confirmados no início do novo período
                detection_count_confirmacao = 0
                analyzed_frames_confirmacao = 0
                #
                continue

            # Fim do preíodo de busca ou confirmação na coordenada
            self.logger.info(f"Final da busca na coordenada {tuple(coordenadas[idx_coord][1:4])}: {len(analyzed_frames)} Frames Analisados com {detection_count} Detecções.")

            # Contabilizando dados do processamento
            qt_analyzed_frames = len(analyzed_frames)
            qt_analyzed_frames_busca = qt_analyzed_frames-analyzed_frames_confirmacao
            qt_detection_count_busca = detection_count-detection_count_confirmacao

            # Salva informações de processamento para os períodos que tiveram confirmação
            if emconfirmacao:
                if camera["ptz_ctrl_ativo"] == 1:
                    dados = [coordenadas[idx_coord][0], qt_analyzed_frames, qt_analyzed_frames_busca, analyzed_frames_confirmacao, detection_count, qt_detection_count_busca, detection_count_confirmacao]
                else:
                    dados = [None, qt_analyzed_frames, qt_analyzed_frames_busca, analyzed_frames_confirmacao, detection_count, qt_detection_count_busca, detection_count_confirmacao]
                # Envia para o banco de dados
                processamentos_id = self.insere_dados_processamentos(self.config_sys, self.config_cam, dados)

            # Verificando o threshold para geração de arquivo MP4
            if (detection_count/qt_analyzed_frames) >= camera["perc_frames_para_mp4"]:
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
                # Metadados exclusivos de câmeras com controle de PTZ ativo
                if camera["ptz_ctrl_ativo"] == 1:
                    metadados["idcoordenadas_cameras"] = coordenadas[idx_coord][0]
                    metadados["desccoordenadas_cameras"] = str(coordenadas[idx_coord][4])
                    metadados["ptz_atual"] = [panA, tiltA, zoomA]
                    metadados["ptz_ajustado"] = [p, t, z]
                metadados["bbox_maior_score"] = bbox_maior_score
                metadados["bbox_media_percentual"] = bbox_media_percentual
                metadados["bbox_media_pixels"] = bbox_media_pixels
                metadados["todas_bboxes"] = todas_bboxes
                # Salva arquivos no disco
                self.save_all_analyzed_video(metadados, analyzed_frames)

            # Atualiza métricas para o Zabbix
            with self.lock:
                self.quadros_processados +=  qt_analyzed_frames
                self.quadros_detectados += detection_count
            # Resetando variáveis por falta de detecção (ovo ciclo de busca)
            analyzed_frames.clear()
            todos_results = []
            detection_count = 0
            # Atualizando dados da câmera
            camera = self.atualiza_dados_camera_ptz(self.config_sys, self.config_cam)
            duracaobusca = camera["duracaobusca"]
            duracaoconfirmacao = camera["duracaoconfirmacao"]
            # Próxima Coordenada PTZ se o controle estiver ativo
            if int(camera["ptz_ctrl_ativo"]) == 1:
                idx_coord = (idx_coord + 1) % len(coordenadas)
                p, t, z = coordenadas[idx_coord][1:4]
                ptzcam.absolute_move( p, t, z )
                time.sleep( camera["ptz_delay_stream"] )
                self.logger.info(f"Início do período de busca na coordenada {coordenadas[idx_coord][1:4]}.")
            else:
                self.logger.warning(f"Controle ativo de PTZ desligado, iniciando o período de busca sem atuação no PTZ.")

            # Restabelecendo o controle de tempo
            agora = time.monotonic()
            deadline = agora + duracaobusca
            emconfirmacao = False
