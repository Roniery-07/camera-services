from cameras.AoFBase import AoFBase
from cameras.CamBaseFixa import CamBaseFixa
import logging
import time
import numpy as np


class CameraFixa(AoFBase, CamBaseFixa):
    def run(self):
        # 1. Initialize Logger
        self.logger = logging.getLogger(self.logger_name)
        self.logger.info(f"Process Started (PID: {self.pid})")

        # 2. Start Grabber Thread
        self.grabber.start()
        time.sleep(3)  # Give FFmpeg time to warm up

        # 3. Connect to RabbitMQ
        # if not self._connect_rabbit():
        #     self.logger.critical("Could not connect to RabbitMQ. Terminating.")
        #     return

        # 4. Initial Camera Config Fetch
        camera = self.update_fixed_camera(self.config_sys, self.config_cam)
        if not camera:
            self.logger.error("Failed to fetch camera config from DB. Exiting.")
            return

        # --- INITIALIZE ALL VARIABLES BEFORE THE LOOP ---
        duracaobusca = camera.get("duracaobusca", 30)
        duracaoconfirmacao = camera.get("duracaoconfirmacao", 30)

        deadline = time.monotonic() + duracaobusca
        emconfirmacao = False

        detection_count = 0
        detection_count_confirmacao = 0
        analyzed_frames_confirmacao = 0
        analyzed_frames = []
        todos_results = []

        fps_target = float(self.config_sys["detector"]["max_fps_processamento"])
        intervalo_entre_frames = 1.0 / fps_target
        proximo_processamento = time.time()

        self.logger.info(f"Início do período de busca. Target FPS: {fps_target}")

        # 5. MAIN LOOP
        while not self.stop_event.is_set():
            agora = time.time()

            # --- CPU REST: Calculate sleep time until next frame ---
            diff = proximo_processamento - agora
            if diff > 0:
                time.sleep(diff)

            # Update the next scheduled time
            proximo_processamento = time.time() + intervalo_entre_frames

            # --- FRAME ACQUISITION ---
            # get_frame() waits up to 5s for the grabber thread
            jpeg_bytes, _, _ = self.get_frame()

            if jpeg_bytes is None:
                # If grabber fails, we just wait for the next cycle
                continue

            # --- AI PROCESSING (RabbitMQ) ---
            response = self.send_frame_to_rabbit(jpeg_bytes, camera)
            results = []
            if response and "results" in response:
                results = response["results"]
                todos_results.append(results)

            # --- DETECTION LOGIC ---
            if len(results) > 0:
                cls_name = self.config_sys["detector"]["CLASSES"][
                    int(results[0]["class"])
                ]
                score = float(results[0]["score"])
                self.logger.warning(f"Detectado {cls_name} ({score:.2f})")
                detection_count += 1
                detection_count_confirmacao += 1

            analyzed_frames_confirmacao += 1
            analyzed_frames.append(jpeg_bytes)

            # --- CYCLE LOGIC (Busca vs Confirmação) ---
            agora_monotonic = time.monotonic()

            if agora_monotonic < deadline:
                continue

            elif detection_count > 0 and not emconfirmacao:
                self.logger.warning(f"Iniciando o período de confirmação.")
                deadline = (
                    time.monotonic() + duracaoconfirmacao
                )  # Reset deadline for confirmation
                emconfirmacao = True
                detection_count_confirmacao = 0
                analyzed_frames_confirmacao = 0
                continue

            # --- END OF CYCLE: Save Metrics and Reset ---
            self.logger.info(
                f"Fim Ciclo: {len(analyzed_frames)} Frames, {detection_count} Detecções."
            )

            qt_analyzed_frames = len(analyzed_frames)
            qt_analyzed_frames_busca = qt_analyzed_frames - analyzed_frames_confirmacao
            qt_detection_count_busca = detection_count - detection_count_confirmacao

            if emconfirmacao:
                # Data for DB: [coord_id, total_f, busca_f, conf_f, total_d, busca_d, conf_d]
                dados = [
                    None,
                    qt_analyzed_frames,
                    qt_analyzed_frames_busca,
                    analyzed_frames_confirmacao,
                    detection_count,
                    qt_detection_count_busca,
                    detection_count_confirmacao,
                ]
                self.insere_dados_processamentos(
                    self.config_sys, self.config_cam, dados
                )

            # Update Shared Value for Supervisor/Zabbix
            with self.quadros_processados.get_lock():
                self.quadros_processados.value += len(analyzed_frames)

            # Cleanup for next cycle
            analyzed_frames.clear()
            todos_results = []
            detection_count = 0

            # Refresh config from DB
            camera = self.update_fixed_camera(self.config_sys, self.config_cam)
            if camera:
                duracaobusca = camera.get("duracaobusca", 30)
                duracaoconfirmacao = camera.get("duracaoconfirmacao", 30)

            self.logger.info(f"Reiniciando busca.")
            deadline = time.monotonic() + duracaobusca
            emconfirmacao = False
