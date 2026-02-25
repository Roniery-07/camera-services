from FFMPEGv.AoFBaseFFMPEG import AoFBaseFFMPEG
from FFMPEGv.CamBaseFixaFFMPEG import CamBaseFixaFFMPEG
import logging
import time
import numpy as np

class CameraFixaFFMPEG(AoFBaseFFMPEG, CamBaseFixaFFMPEG):

    def run(self):
        # 1. INITIALIZATION INSIDE PROCESS
        self.logger = logging.getLogger(self.logger_name)
        self.logger.info(f"Process Started (PID: {self.pid})")

        self.grabber.start()
        time.sleep(3)

        if not self._connect_rabbit(): # Modify _connect_rabbit to return True/False
            self.logger.critical("Could not connect to RabbitMQ. Terminating process.")
            return
        # Update Config
        camera = self.atualiza_dados_camera_fixa(self.config_sys, self.config_cam)
        if not camera:
            self.logger.error("Failed to fetch camera config from DB. Exiting.")
            return

        duracaobusca = camera["duracaobusca"]
        duracaoconfirmacao = camera["duracaoconfirmacao"]

        # Timers
        deadline = time.monotonic() + duracaobusca
        emconfirmacao = False

        # FPS Control
        fps_target = float(self.config_sys["detector"]["max_fps_processamento"])
        intervalo_entre_frames = 1.0 / fps_target
        proximo_processamento = time.time()
        
        # Shared Memory Deduplication
        ultimo_frame_bytes = None

        # Data Containers
        detection_count = 0
        detection_count_confirmacao = 0
        analyzed_frames_confirmacao = 0 
        analyzed_frames = []
        todos_results = []
        
        self.logger.info(f"Início do período de busca.")

        # 2. MAIN LOOP
        while not self.stop_event.is_set():
            # Idle sleep
            time.sleep(0.01)
            
            # Rate Limit
            tempo_agora = time.time()
            if tempo_agora < proximo_processamento:
                continue

            proximo_processamento = max(tempo_agora + intervalo_entre_frames, proximo_processamento + intervalo_entre_frames)

            self.logger.info(f"prox processament: {proximo_processamento} - intervalor entre frames: {intervalo_entre_frames} ")
            # Get Frame
            frame, frame_bytes, _ = self.get_frame()        
            if frame is None:
                self.logger.info("frame is none")
                continue

            # Deduplicate
#            if frame_bytes == ultimo_frame_bytes:
#                continue

            # Update State
            ultimo_frame_bytes = frame_bytes
            # Send to AI
            response = self.send_frame_to_rabbit(frame, camera)
            print(f"reponse: {response}")
            #self.logger.info(f"Sending data to AI - Length: {len(frame)}")
            results = []
            if response and 'results' in response:
                results = response['results']
                todos_results.append(results)

            # Check Detections
            if len(results) > 0:
                cls_name = self.config_sys['detector']['CLASSES'][int(results[0]['class'])]
                score = float(results[0]['score'])
                self.logger.warning(f"Detectado {cls_name} ({score:.2f})")
                
                detection_count += 1
                detection_count_confirmacao += 1
            
            analyzed_frames_confirmacao += 1
            
            # Store frame (Consider disabling this if RAM issues occur)
            analyzed_frames.append(frame.copy())

            # Logic Gate / Timers
            agora_monotonic = time.monotonic()

            if agora_monotonic < deadline:
                continue
            
            # Transition to Confirmation Mode?
            elif detection_count > 0 and not emconfirmacao:
                self.logger.warning(f"Iniciando o período de confirmação.")
                deadline += duracaoconfirmacao
                emconfirmacao = True
                detection_count_confirmacao = 0
                analyzed_frames_confirmacao = 0
                continue

            # End of Cycle
            self.logger.info(f"Fim Ciclo: {len(analyzed_frames)} Frames, {detection_count} Detecções.")

            # Metrics & DB
            qt_analyzed_frames = len(analyzed_frames)
            qt_analyzed_frames_busca = qt_analyzed_frames - analyzed_frames_confirmacao
            qt_detection_count_busca = detection_count - detection_count_confirmacao

            if emconfirmacao:
                dados = [None, qt_analyzed_frames, qt_analyzed_frames_busca, analyzed_frames_confirmacao, detection_count, qt_detection_count_busca, detection_count_confirmacao]
                self.insere_dados_processamentos(self.config_sys, self.config_cam, dados)
                
                # Video Saving Logic would go here...

            # Update Shared Metrics safely
            with self.quadros_processados.get_lock():
                self.quadros_processados.value += len(analyzed_frames)
            
            # Reset
            analyzed_frames.clear()
            todos_results = []
            detection_count = 0
            
            # Refresh Config
            camera = self.atualiza_dados_camera_fixa(self.config_sys, self.config_cam)
            if camera:
                duracaobusca = camera["duracaobusca"]
                duracaoconfirmacao = camera["duracaoconfirmacao"]

            self.logger.info(f"Reiniciando busca.")
            deadline = time.monotonic() + duracaobusca
            emconfirmacao = False
