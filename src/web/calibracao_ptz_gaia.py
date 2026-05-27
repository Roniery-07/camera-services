import cv2
import math
from flask import Flask, render_template_string, Response, request, jsonify
from sensecam_control import onvif_control

zm_monitor_id = 29
CAM = onvif_control.CameraControl("172.16.106.100", "service", "Issp2006!")
CAM.camera_start()

app = Flask(__name__)

bbox = [100, 100, 200, 200]
show_bbox = True
ptz_destino = None


def ptz_tracking(w, h, a, b, c, pmin, pmax, tmin, tmax, z, zmax, eta=3, alpha=None):
    """Compute pan-tilt-zoom displacement to center object.

    Args:
        w (float): image width [pixels]
        h (float): image height [pixels]
        a (float): object width [pixels]
        b (float): object height [pixels]
        c (2-tuple of float): object center horizontal and vertical coordinates [pixels]
        pmin (float): image minimum camera pan coordinate
        pmax (float): image maximum camera pan coordinate
        tmin (float): image minimum camera tilt coordinate
        tmax (float): image maximum camera tilt coordinate
        z (float): current camera space zoom
        zmax (float): image maximum camera zoom coordinate
        eta (float, optional): target image fraction size reference (default is 3)
        alpha (2-tuple of float, optional): camera horizontal and vertical field of view [degrees] (default is None)

    Returns:
        (float): pan displacement
        (float): tilt displacement
        (float): zoom displacement
    """

    # Pan-tilt limits from field of view.
    if pmin is None or pmax is None or tmin is None or tmax is None:
        pmin = 0
        pmax = alpha[0] / 180 / (zmax**z)
        tmin = 0
        tmax = alpha[1] / 71.25 / (zmax**z)

    # Compute camera space displacements to center object.
    dp = (1 / 2 - c[0] / w) * (pmax - pmin)
    dt = (1 / 2 - c[1] / h) * (tmax - tmin)
    dz = math.log(
        1
        + max(min(w / eta / a, h / eta / b, zmax ** (1 - z)), zmax ** (-z)) / (zmax**z)
    ) / math.log(zmax)

    return dp, dt, dz


def move_camera_to_track_object(bbox, ptzc, eta=3):
    """Set camera relative move to track an object.

    Args:
        camera (class): camera class
        bbox (4-tuple of int): object bounding box [pixels]
        eta (float, optional): target image fraction size reference (default is 3)
    """
    w, h = 1280, 720
    z = ptzc[2]
    pmin, pmax = (None, None)
    tmin, tmax = (None, None)
    zmax = 45
    alpha = (61.8, 37.1)

    # Get current camera parameters.
    # camera_parameters = camera.get_current_camera_parameters()
    # w, h = camera_parameters['image_size']
    # z = camera_parameters['current_camera_space_zoom']
    # pmin, pmax = camera_parameters['image_pan_space']
    # tmin, tmax = camera_parameters['image_tilt_space']
    # zmax = camera_parameters['maximum_zoom_factor']
    # alpha = camera_parameters['field_of_view']

    # Get bounding box size and center.
    a = bbox[2] - bbox[0]  # bounding box width [pixels]
    b = bbox[3] - bbox[1]  # bounding box height [pixels]
    c = (
        (bbox[0] + bbox[2]) / 2,
        (bbox[1] + bbox[3]) / 2,
    )  # bounding box center [pixels]

    # Calculate relative pan-tilt-zoom displacements.
    dp, dt, dz = ptz_tracking(
        w, h, a, b, c, pmin, pmax, tmin, tmax, z, zmax, eta, alpha
    )

    # Set camera pan-tilt-zoom displacement command.
    return ptzc[0] - dp, ptzc[1] + dt, ptzc[2] + dz


def gen_frames():
    global bbox, show_bbox
    cap = cv2.VideoCapture(
        f"http://zm.apagaofogo.eco.br/zm/cgi-bin/nph-zms?mode=jpeg&monitor={zm_monitor_id}&scale=100&user=apagaofogo&pass=sng2Bu1Gyb4TCQPDrdC5HiXhIXW8hf"
    )
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    while True:
        success, frame = cap.read()
        if not success:
            break
        frame = cv2.resize(frame, (1280, 720))
        if show_bbox and bbox and len(bbox) == 4:
            x1, y1, x2, y2 = map(int, bbox)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        ret, buffer = cv2.imencode(".jpg", frame)
        frame = buffer.tobytes()
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
    cap.release()


HTML_PAGE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <title>Camera PTZ Web</title>
  <style>
    body {
      margin: 0;
      padding: 0;
      font-family: sans-serif;
      background: #f8f8f8;
    }
    .stream-container {
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: row;
      align-items: flex-start;
    }
    #canvas-container {
      position: relative;
    }
    #streamimg {
      width: 1280px;
      height: 720px;
      background: #000;
      display: block;
      object-fit: cover;
      object-position: left top;
    }
    #bboxCanvas {
      position: absolute;
      left: 0;
      top: 0;
      width: 1280px;
      height: 720px;
      z-index: 10;
      cursor: crosshair;
    }
    form, #result {
      margin-left: 32px;
    }
    form label {
      display: block;
      margin: 8px 0 2px 0;
    }
    button {
      margin: 6px 4px 6px 0;
    }
  </style>
</head>
<body>
  <h1 style="margin-left:12px;">Camera Stream & PTZ</h1>
  <div class="stream-container">
    <div id="canvas-container">
      <img id="streamimg" src="{{ url_for('video_feed') }}">
      <canvas id="bboxCanvas" width="1280" height="720"></canvas>
    </div>
    <form id="argsForm" autocomplete="off">
      <label>BBox: <input name="bbox" id="bboxInput" value="100,100,200,200"></label>
      <label>PTZ: <input id="ptz" name="ptz" value="0.067,0.771,0.032"></label>
      <label>Frame Size: <input name="frame_size" value="1280,720"></label>
      <label>FOV V: <input name="fov_v" value="37.1"></label>
      <label>FOV H: <input name="fov_h" value="61.8"></label>
      <label>Proporção alvo: <input name="proporcao_alvo" value="0.5"></label>
      <label>Zoom máximo: <input name="zoom_maximo" value="0.6"></label>
      <label>Zoom mínimo: <input name="zoom_minimo" value="0.0"></label>
      <div>
        <button type="button" onclick="drawBbox()">Exibir BBox</button>
        <button type="button" onclick="calcularPTZ()">Calcular PTZ</button>
        <button type="button" onclick="focarPTZ()">Focar PTZ</button>
        <button type="button" onclick="PegarPTZ()">Obter PTZ</button>
      </div>
    </form>
  </div>
  <div id="result" style="margin-left:12px;margin-top:12px;"></div>
<script>
function getFormData() {
  const form = document.getElementById('argsForm');
  const data = Object.fromEntries(new FormData(form));
  data['bbox'] = data['bbox'].split(',').map(x=>x.trim());
  data['ptz'] = data['ptz'].split(',').map(x=>x.trim());
  data['frame_size'] = data['frame_size'].split(',').map(x=>x.trim());
  return data;
}
function drawBbox() {
  const bboxAtual = getFormData()['bbox'];
  fetch('/show_bbox', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({show_bbox: true, bbox: bboxAtual})
  });
}
function calcularPTZ() {
  fetch('/calcular_ptz', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify(getFormData())
  })
  .then(resp=>resp.json())
  .then(res=>{
    document.getElementById('result').innerText = "PTZ calculado: " + res.ptz_destino;
  });
}
function focarPTZ() {
  fetch('/focar_ptz', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify(getFormData())
  })
  .then(resp=>resp.json())
  .then(res=>{
    document.getElementById('result').innerText = "PTZ focado: " + res.ptz_destino;
  });
}
window.onload = function() {
  drawBbox();
};
function PegarPTZ() {
  fetch('/pega_ptz', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify(getFormData())
  })
  .then(resp=>resp.json())
  .then(res=>{
    document.getElementById('result').innerText = "PTZ obtido: " + res.ptz_atualizado;
    document.getElementById('ptz').value = res.ptz_atualizado;
  });
}
const canvas = document.getElementById('bboxCanvas');
const ctx = canvas.getContext('2d');
let startX, startY, isDrawing = false;

canvas.addEventListener('mousedown', e => {
  startX = e.offsetX;
  startY = e.offsetY;
  isDrawing = true;
});

canvas.addEventListener('mousemove', e => {
  if (!isDrawing) return;
  const mouseX = e.offsetX;
  const mouseY = e.offsetY;
  const width = mouseX - startX;
  const height = mouseY - startY;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = 'red';
  ctx.lineWidth = 2;
  ctx.strokeRect(startX, startY, width, height);
});

canvas.addEventListener('mouseup', e => {
  if (!isDrawing) return;
  isDrawing = false;
  const endX = e.offsetX;
  const endY = e.offsetY;
  const x = Math.min(startX, endX);
  const y = Math.min(startY, endY);
  const w = Math.abs(endX - startX);
  const h = Math.abs(endY - startY);
  document.getElementById('bboxInput').value = `${x},${y},${x + w},${y + h}`;
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/video_feed")
def video_feed():
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/calcular_ptz", methods=["POST"])
def calcular_ptz():
    global ptz_destino, bbox
    data = request.json
    bbox = [int(v) for v in data["bbox"]]
    ptz = [float(v) for v in data["ptz"]]
    frame_size = [int(v) for v in data["frame_size"]]
    fov_v = float(data["fov_v"])
    fov_h = float(data["fov_h"])
    proporcao_alvo = float(data["proporcao_alvo"])
    zoom_maximo = float(data["zoom_maximo"])
    zoom_minimo = float(data["zoom_minimo"])

    # ptz_destino = recalcular_e_ajustar_ptz(bbox, ptz, frame_size, fov_v, fov_h, proporcao_alvo, zoom_maximo, zoom_minimo)
    p, t, z = move_camera_to_track_object(bbox, ptz)
    CAM.absolute_move(p, t, z)
    #    CAM.absolute_move(ptz_destino[0], ptz_destino[1], ptz_destino[2])
    ptz_destino = [p, t, z]
    return jsonify({"ptz_destino": ptz_destino})
    print(f"PTZ Origem: {ptz}")
    print(f"PTZ Destin: {ptz_destino}")


@app.route("/show_bbox", methods=["POST"])
def toggle_bbox():
    global show_bbox, bbox
    req = request.json
    show_bbox = req["show_bbox"]
    if "bbox" in req:
        bbox = [int(v) for v in req["bbox"]]
    return jsonify({"status": "ok", "show_bbox": show_bbox})


@app.route("/focar_ptz", methods=["POST"])
def focar_ptz():
    global ptz_destino
    data = request.json
    ptz_destino = [float(v) for v in data["ptz"]]
    CAM.absolute_move(ptz_destino[0], ptz_destino[1], ptz_destino[2])
    return jsonify({"ptz_destino": ptz_destino})


@app.route("/pega_ptz", methods=["POST"])
def pega_ptz():
    ptz = CAM.get_ptz()
    p, t, z = ptz
    tptz = f"{p}, {t}, {z}"
    print(tptz)
    return jsonify({"ptz_atualizado": tptz})


if __name__ == "__main__":
    app.run(debug=True, port=5001, host="0.0.0.0")
