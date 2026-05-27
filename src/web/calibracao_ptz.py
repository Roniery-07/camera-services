import cv2
from flask import Flask, render_template_string, Response, request, jsonify
from sensecam_control import onvif_control

zm_monitor_id = 25
CAM = onvif_control.CameraControl("172.16.101.5", "service", "Issp2006!")
CAM.camera_start()

app = Flask(__name__)

bbox = [100, 100, 200, 200]
show_bbox = True
ptz_destino = None


def recalcular_e_ajustar_ptz(
    bbox,
    ptz,
    frame_size,
    fov_v,
    fov_h,  # fov_v = 37.1, fov_h = 61.8 (Bosch Autodome 5100i IR, wide)
    proporcao_alvo,
    zoom_maximo,
    zoom_minimo,
):
    # FOVs reais Bosch Autodome 5100i IR
    fov_v_min = fov_v  # 37.1 graus (wide)
    fov_v_max = 1.3  # 1.3 graus (tele)
    fov_h_min = fov_h  # 61.8 graus (wide)
    fov_h_max = 2.3  # 2.3 graus (tele)
    PAN_RANGE_GRAUS = 180.0
    TILT_RANGE_GRAUS = 90.0

    x_min, y_min, x_max, y_max = bbox
    pan_atual, tilt_atual, zoom_atual = ptz
    largura, altura = frame_size

    # Cambalacho dinâmico feito no zoom_máximo, para aumentar de acordo com o zoom_atual
    # algo que aparentemente ajuda na centralização e foco da imagem...
    # Obs.: não tenho explicação pra isso...
    # zoom_maximo = min(zoom_maximo + (zoom_atual ** 1.7) * 0.7, 1.0)
    zoom_maximo = min(zoom_atual, 1.0)

    # Interpolação calibrada do FOV
    def interpolar_fov(zoom, zoom_min, zoom_max, fov_min, fov_max):
        if zoom_max == zoom_min:
            return fov_min
        t = (zoom - zoom_min) / (zoom_max - zoom_min)
        return fov_min + t * (fov_max - fov_min)

    fov_v_atual = interpolar_fov(
        zoom_atual, zoom_minimo, zoom_maximo, fov_v_min, fov_v_max
    )
    fov_h_atual = interpolar_fov(
        zoom_atual, zoom_minimo, zoom_maximo, fov_h_min, fov_h_max
    )

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
    delta_tilt_graus = (
        -(y_bb - y_centro) * graus_por_px_v
    )  # Sinal negativo para coordenada de imagem

    # Normalização
    delta_pan_norm = delta_pan_graus / PAN_RANGE_GRAUS
    delta_tilt_norm = delta_tilt_graus / TILT_RANGE_GRAUS

    # pan_novo = pan_atual + delta_pan_norm
    # tilt_novo = tilt_atual + delta_tilt_norm

    def wrap_circular(valor):
        # Mapeia qualquer valor para o intervalo [-1.0, 1.0)
        return ((valor + 1.0) % 2.0) - 1.0

    pan_novo = wrap_circular(pan_atual + delta_pan_norm)
    tilt_novo = wrap_circular(tilt_atual + delta_tilt_norm)

    # pan_novo = max(min(pan_novo, 1.0), -1.0)
    # tilt_novo = max(min(tilt_novo, 1.0), -1.0)

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

    print(
        "Recalculando PTZ:",
        bbox,
        ptz,
        frame_size,
        f"fov_v_atual={fov_v_atual:.6f}",
        f"fov_h_atual={fov_h_atual:.6f}",
        "graus_por_px_h",
        graus_por_px_h,
        "graus_por_px_v",
        graus_por_px_v,
        proporcao_alvo,
        zoom_maximo,
        zoom_minimo,
        "=>",
        pan_novo,
        tilt_novo,
        zoom_novo,
    )
    return [round(pan_novo, 3), round(tilt_novo, 3), round(zoom_novo, 3)]


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
    ptz_destino = recalcular_e_ajustar_ptz(
        bbox, ptz, frame_size, fov_v, fov_h, proporcao_alvo, zoom_maximo, zoom_minimo
    )
    CAM.absolute_move(ptz_destino[0], ptz_destino[1], ptz_destino[2])
    return jsonify({"ptz_destino": ptz_destino})


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
