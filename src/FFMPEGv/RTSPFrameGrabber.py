import multiprocessing as mp
import sys
import threading
from multiprocessing import shared_memory
import subprocess as sp
import time
import logging
import os
import select # Required for timeout logic

class RTSPFrameGrabber(threading.Thread):
    def __init__(self, rtsp_url, shm_name, frame_shape, lock, stop_event, logger_name, ffmpeg_bin="ffmpeg"):
        super().__init__()
        self.rtsp_url = rtsp_url
        self.shm_name = shm_name
        
        # FIX: frame_shape comes from numpy as (Height, Width, Channels)
        self.height = frame_shape[0] 
        self.width = frame_shape[1]
        
        self.lock = lock
        self.stop_event = stop_event
        self.logger_name = logger_name
        self.ffmpeg_bin = ffmpeg_bin
        
        # Calculate YUV420p Buffer Size (Width * Height * 1.5)
        self.frame_byte_size = int(self.width * self.height * 1.5)

    def stop_ffmpeg(self, process):
        """Terminates ffmpeg gracefully, similar to Frigate's logic."""
        if process is None:
            return
            
        logger = logging.getLogger(self.logger_name)
        try:
            logger.info("Terminating ffmpeg process...")
            process.terminate()
            # Wait up to 5 seconds for graceful exit
            try:
                process.communicate(timeout=5)
            except sp.TimeoutExpired:
                logger.warning("FFmpeg didn't exit. Force killing...")
                process.kill()
                process.communicate()
        except Exception as e:
            logger.error(f"Error stopping ffmpeg: {e}")

    def run(self):
        logger = logging.getLogger(self.logger_name)
        
        # Attach to Shared Memory
        try:
            existing_shm = shared_memory.SharedMemory(name=self.shm_name)
            shm_buffer = existing_shm.buf
        except Exception as e:
            logger.error(f"[Grabber] Could not attach to shared memory: {e}")
            return

        logger.info(f"[Grabber] Process started for: {self.rtsp_url}")

        while not self.stop_event.is_set():
            process = None
            try:
                ffmpeg_cmd = [
                    self.ffmpeg_bin,
                    "-hide_banner", "-loglevel", "warning",
                    
                    # 1. Thread Limiting (Prevents CPU context-switching bloat)
                    "-threads", "2",
                    
                    # 2. Network Stability
                    "-rtsp_transport", "tcp",   
                    "-timeout", "10000000",      
                    
                    # 3. Hardware Decode
                    # Decodes on GPU, then safely dumps to RAM as nv12 
                    # so the software scaler can take over.
                    "-hwaccel", "cuda",               
                    "-hwaccel_output_format", "nv12", 
                    
                    "-i", self.rtsp_url,        
                    
                    # 4. Filter Chain (Drop frames FIRST, then scale)
                    "-vf", f"fps=5,scale={self.width}:{self.height}", 
                    
                    # 5. Output Formatting
                    "-threads", "2",
                    "-f", "rawvideo",
                    "-pix_fmt", "bgr24",        
                    "-"
                ] 
                process = sp.Popen(
                    ffmpeg_cmd, 
                    stdout=sp.PIPE, 
                    stderr=sys.stderr, 
                    bufsize=self.frame_byte_size * 10 # Buffer 10 frames like Frigate
                )

                while not self.stop_event.is_set():
                    # 1. VERIFICATION: Check if process is still alive
                    if process.poll() is not None:
                        logger.error("[Grabber] FFmpeg process crashed/exited unexpectedly.")
                        break

                    # 2. VERIFICATION: Read with Timeout (Watchdog logic)
                    # This prevents the thread from hanging forever if the camera creates a TCP half-open connection
                    ready, _, _ = select.select([process.stdout], [], [], 10.0) # 5 second timeout
                    
#                    if not ready:
#                        logger.warning("[Grabber] Timeout reading from FFmpeg (Camera stalled). Restarting.")
#                        break

                    # 3. Read Frame
                    raw_bytes = process.stdout.read(self.frame_byte_size)

                    if len(raw_bytes) != self.frame_byte_size:
                        logger.error(f"[Grabber] Incomplete frame. Expected {self.frame_byte_size}, got {len(raw_bytes)}")
                        break

                    # Critical Section: Write to Shared Memory
                    with self.lock:
                        shm_buffer[:self.frame_byte_size] = raw_bytes

            except Exception as e:
                logger.error(f"[Grabber] Error: {e}")
                time.sleep(2) # Penalty wait on error
            
            finally:
                # Use the new helper to clean up processes properly
                self.stop_ffmpeg(process)
                
                if not self.stop_event.is_set():
                    # Small sleep before restart to prevent tight loop CPU spike if network is down
                    time.sleep(1)
        
        # Cleanup
        try:
            existing_shm.close()
        except:
            pass
        logger.info("[Grabber] Process stopped")
