import threading
import subprocess as sp
import logging
import time
import fcntl
import os  # Added for environment passing
from multiprocessing import shared_memory

class RTSPFrameGrabber(threading.Thread):
    PENALTY_BASE        = 2
    PENALTY_MAX         = 60
    PENALTY_MULTIPLIER  = 2
    HEALTHY_STREAK      = 10
    CORRUPT_TOLERANCE   = 5

    # MJPEG frame boundaries
    SOI = b'\xff\xd8'  # Start of Image
    EOI = b'\xff\xd9'  # End of Image

    def __init__(self, rtsp_url, shm_name, shm_size, lock, stop_event, width, height,
                 logger_name, frame_ready_event, ffmpeg_bin="ffmpeg"):
        super().__init__()
        self.rtsp_url          = rtsp_url
        self.shm_name          = shm_name
        self.shm_size          = shm_size
        self.lock              = lock
        self.width             = width
        self.true_height       = height 
        self.stop_event        = stop_event
        self.logger_name       = logger_name
        self.ffmpeg_bin        = ffmpeg_bin
        self.frame_ready_event = frame_ready_event

        self._penalty_seconds    = self.PENALTY_BASE
        self._healthy_frames     = 0
        self._corrupt_count      = 0

    def _record_failure(self, logger, reason: str):
        logger.warning(f"[Grabber] Failure: {reason}. Waiting {self._penalty_seconds}s before restart.")
        self._healthy_frames = 0
        self._corrupt_count  = 0
        time.sleep(self._penalty_seconds)
        self._penalty_seconds = min(self._penalty_seconds * self.PENALTY_MULTIPLIER, self.PENALTY_MAX)

    def _record_healthy_frame(self):
        self._healthy_frames += 1
        if self._healthy_frames >= self.HEALTHY_STREAK:
            self._penalty_seconds = self.PENALTY_BASE
            self._healthy_frames  = 0

    def _record_corrupt_frame(self, logger) -> bool:
        self._corrupt_count  += 1
        self._healthy_frames  = 0
        logger.warning(f"[Grabber] Corrupt frame {self._corrupt_count}/{self.CORRUPT_TOLERANCE}")
        return self._corrupt_count >= self.CORRUPT_TOLERANCE

    def _stop_ffmpeg(self, process):
        if process is None:
            return
        logger = logging.getLogger(self.logger_name)
        try:
            logger.info("Terminating ffmpeg process...")
            process.terminate()
            try:
                process.communicate(timeout=5)
            except sp.TimeoutExpired:
                process.kill()
                process.communicate()
        except Exception as e:
            logger.error(f"Error stopping ffmpeg: {e}")

    def _read_mjpeg_frame(self, process, logger) -> bytes | None:
        """
        Efficiently reads bytes from ffmpeg stdout using a mutable bytearray.
        """
        # Increased chunk size to 64KB to reduce Python loop overhead
        CHUNK_SIZE = 65536
        buf = bytearray()
        
        while not self.stop_event.is_set():
            chunk = process.stdout.read(CHUNK_SIZE)
            if not chunk:
                return None 

            buf.extend(chunk)

            # Find start of image
            start = buf.find(self.SOI)
            if start == -1:
                # Discard all but the last byte to keep logic fast
                del buf[:-1]
                continue

            if start > 0:
                del buf[:start]

            # Find end of image
            end = buf.find(self.EOI)
            if end == -1:
                continue 

            # Extract full frame and cleanup buffer
            frame_bytes = bytes(buf[:end + 2])
            del buf[:end + 2] 
            return frame_bytes

        return None

    def run(self):
        logger = logging.getLogger(self.logger_name)

        try:
            existing_shm = shared_memory.SharedMemory(name=self.shm_name)
            shm_buffer = existing_shm.buf
        except Exception as e:
            logger.error(f"[Grabber] Could not attach to shared memory: {e}")
            return

        logger.info(f"[Grabber] Started for: {self.rtsp_url}")

        while not self.stop_event.is_set():
            process = None
            restart_reason = None

            try:
                self._corrupt_count = 0

                ffmpeg_cmd = [
                    self.ffmpeg_bin,
                    "-hide_banner",
                    "-loglevel", "fatal",
                    "-nostats", # Reduce stderr noise
                    "-rtsp_transport", "tcp",
                    "-timeout", "10000000",
                    "-fflags", "+genpts+discardcorrupt",
                    "-err_detect", "ignore_err",
                    "-hwaccel", "cuda",
                    "-hwaccel_output_format", "nv12",
                    "-i", self.rtsp_url,
                    "-vf", f"fps=4,scale={self.width}:{self.true_height}",
                    "-c:v", "mjpeg",
                    "-q:v", "10",
                    "-f", "image2pipe",
                    "-"
                ]

                # Pass full environment so CUDA is found correctly
                process = sp.Popen(
                    ffmpeg_cmd,
                    stdout=sp.PIPE,
                    stderr=sp.DEVNULL, # Set to DEVNULL as requested
                    bufsize=10 * 1024 * 1024,
                    env=os.environ.copy() 
                )

                # Expand OS pipe buffer
                try:
                    fcntl.fcntl(process.stdout.fileno(), 1031, 10 * 1024 * 1024)
                except OSError:
                    pass

                while not self.stop_event.is_set():
                    if process.poll() is not None:
                        restart_reason = "ffmpeg process exited"
                        break

                    jpeg_bytes = self._read_mjpeg_frame(process, logger)
                    if jpeg_bytes is None:
                        restart_reason = "stream stalled or pipe closed"
                        break

                    frame_size = len(jpeg_bytes)
                    if frame_size + 4 > self.shm_size:
                        if self._record_corrupt_frame(logger):
                            restart_reason = "too many oversized frames"
                            break
                        continue

                    self._record_healthy_frame()

                    with self.lock:
                        shm_buffer[:4] = frame_size.to_bytes(4, byteorder='little')
                        shm_buffer[4:4 + frame_size] = jpeg_bytes
                        self.frame_ready_event.set()

            except Exception as e:
                restart_reason = f"exception: {e}"
                logger.error(f"[Grabber] Unexpected error: {e}")
            finally:
                self._stop_ffmpeg(process)

            if not self.stop_event.is_set() and restart_reason:
                self._record_failure(logger, restart_reason)

        try:
            existing_shm.close()
        except Exception:
            pass
        logger.info("[Grabber] Stopped.")
