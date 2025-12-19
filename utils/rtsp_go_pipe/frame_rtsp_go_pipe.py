import threading
import struct
import subprocess
import time
from flask import Flask, Response, render_template_string

app = Flask(__name__)

latest_frame = None
latest_timestamp = ""

def go_reader():
    global latest_frame, latest_timestamp
    # Executa o binário Go (ajuste o nome/path do binário!)
    proc = subprocess.Popen(
        ["./rtsp_to_pipe"],  # binário Go compilado
        stdout=subprocess.PIPE,
        bufsize=0
    )
    f = proc.stdout
    while True:
        ts_bytes = f.read(8)
        if not ts_bytes:
            continue
        timestamp = struct.unpack(">d", ts_bytes)[0]
        latest_timestamp = f"{timestamp:.3f} segundos"
        size_bytes = f.read(4)
        frame_size = struct.unpack(">I", size_bytes)[0]
        frame_data = b""
        while len(frame_data) < frame_size:
            chunk = f.read(frame_size - len(frame_data))
            if not chunk:
                break
            frame_data += chunk
        latest_frame = frame_data

def frame_generator():
    global latest_frame
    while True:
        if latest_frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + latest_frame + b'\r\n')
        time.sleep(0.05)

def status_generator():
    last = None
    while True:
        if latest_timestamp != last:
            yield f"data: {latest_timestamp}\n\n"
            last = latest_timestamp
        time.sleep(0.05)

@app.route('/')
def index():
    return render_template_string("""
    <html>
    <head>
        <title>Frame + Timestamp via PIPE Go</title>
        <style>.dados { font-family: monospace; font-size: 1.2em; margin-top: 15px; }</style>
    </head>
    <body>
        <h2>Streaming com Timestamp do Go/gortsplib via PIPE</h2>
        <img src="/video_feed" width="800" />
        <div class="dados">Timestamp frame: <span id="ts_frame"></span></div>
        <script>
        if (!!window.EventSource) {
            var source = new EventSource('/status_feed');
            source.onmessage = function(e) {
                document.getElementById('ts_frame').textContent = e.data;
            }
        }
        </script>
    </body>
    </html>
    """)

@app.route('/video_feed')
def video_feed():
    return Response(frame_generator(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/status_feed')
def status_feed():
    return Response(status_generator(), mimetype='text/event-stream')

if __name__ == '__main__':
    threading.Thread(target=go_reader, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, threaded=True)
