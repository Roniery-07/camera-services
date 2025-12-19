import threading
import subprocess as sp
import cv2
import numpy as np
import time
from datetime import datetime

class RTSPFrameGrabber:
    def __init__(self, rtsp_url, frame_shape, ffmpeg_bin="ffmpeg", reconnect_interval=5):
        self.rtsp_url = rtsp_url
        self.frame_shape = frame_shape  # (height, width, channels)
        self.ffmpeg_bin = ffmpeg_bin
        self.reconnect_interval = reconnect_interval
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        if not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def get_frame(self):
        with self._frame_lock:
            if self._latest_frame is not None:
                return self._latest_frame.copy()
            else:
                return None

    def _run(self):
        while True:
            ffmpeg_cmd = [
                self.ffmpeg_bin,
                "-hide_banner",
                "-loglevel", "error",
                "-threads", "1",
                "-rtsp_transport", "tcp",
                "-an",                   # Desativa o áudio!
                "-c:v", "h264_cuvid",    # GPU decoder ativado!
                "-i", self.rtsp_url,
#                "-r", "2",               # 2 FPS
                "-f", "rawvideo",
#                "-pix_fmt", "yuv420p",
#                "-pix_fmt", "bgr24",
                "-"
            ]
            try:
                process = sp.Popen(
                    ffmpeg_cmd,
                    stdout=sp.PIPE,
                    stderr=sp.PIPE,
                    bufsize=10**8
                )
                frame_size = int(np.prod(self.frame_shape))
                while True:
                    raw_frame = process.stdout.read(frame_size)
                    if len(raw_frame) != frame_size:
                        raise RuntimeError("Frame size mismatch or stream ended.")
                    frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape(self.frame_shape)
                    with self._frame_lock:
                        self._latest_frame = frame
            except Exception as e:
                print(f"RTSPFrameGrabber: Error: {e}, reconnecting in {self.reconnect_interval}s")
                time.sleep(self.reconnect_interval)
            finally:
                if 'process' in locals():
                    process.kill()
                    process.wait()

## Exemplo de uso:
#if __name__ == "__main__":
#    # Ajuste para as dimensões do seu vídeo (Ex: 720p RGB: (720, 1280, 3))
#    rtsp_url = "rtsp://10.0.5.246:2000/AoF-C0029-APASC?username=admin&password=4jAMH@h*W6@$W3K"
#    shape = (720, 1280, 3)
#    grabber = RTSPFrameGrabber(rtsp_url, shape)
#    grabber.start()
#
#    last_frame_id = None
#    while True:
#        frame = grabber.get_frame()
#        if frame is not None:
#            # Para evitar informações repetidas se o frame não mudou,
#            # você pode comparar o conteúdo ou só o id (hash) do array.
#            frame_id = frame.__array_interface__['data'][0]
#            if frame_id != last_frame_id:
#                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#                print(f"[{timestamp}] Frame adquirido: shape={frame.shape}, dtype={frame.dtype}")
#                last_frame_id = frame_id
#            time.sleep(0.5)  # 2 FPS (0.5s de intervalo)
#        else:
#            # Caso não tenha frame ainda, aguarde um pouco.
#            time.sleep(0.1)

def monitor_stream(stream_id, grabber):
    last_frame_id = None
    while True:
        frame = grabber.get_frame()
        if frame is not None:
            frame_id = frame.__array_interface__['data'][0]
            if frame_id != last_frame_id:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] Stream {stream_id}: Frame adquirido: shape={frame.shape}, dtype={frame.dtype}")
                last_frame_id = frame_id
            time.sleep(0.5)  # 2 FPS
        else:
            time.sleep(0.1)

if __name__ == "__main__":
    # Substitua pelas URLs dos seus três streamings RTSP:
    rtsp_urls = [
        "rtsp://10.0.5.246:2000/AoF-C0029-APASC?username=admin&password=4jAMH@h*W6@$W3K",
        "rtsp://10.0.5.246:2000/D619-C0018-CA?username=admin&password=4jAMH@h*W6@$W3K",
        "rtsp://10.0.5.246:2000/D619-C0019-CA?username=admin&password=4jAMH@h*W6@$W3K"
    ]
    shape = (720, 1280, 3)  # ajuste conforme necessário

    grabbers = []
    threads = []
    for i, url in enumerate(rtsp_urls, start=1):
        grabber = RTSPFrameGrabber(url, shape)
        grabber.start()
        grabbers.append(grabber)
        t = threading.Thread(target=monitor_stream, args=(i, grabber), daemon=True)
        t.start()
        threads.append(t)

    # Mantém o programa rodando
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Encerrando monitoramento de streams.")
