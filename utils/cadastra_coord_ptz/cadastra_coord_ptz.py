from flask import Flask, render_template_string, Response, request, jsonify
import cv2
import mysql.connector
from sensecam_control import onvif_control

zm_monitor_id = 25
CAM = onvif_control.CameraControl('172.16.101.5', 'service', 'Issp2006!')

CAM.camera_start()
app = Flask(__name__)

def gen_frames():
    cap = cv2.VideoCapture(f"https://zm.apagaofogo.eco.br/zm/cgi-bin/nph-zms?mode=jpeg&monitor={zm_monitor_id}&scale=100&user=apagaofogo&pass=sng2Bu1Gyb4TCQPDrdC5HiXhIXW8hf")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    while True:
        success, frame = cap.read()
        if not success:
            break
        frame = cv2.resize(frame, (1280, 720))
        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    cap.release()

HTML_PAGE = '''
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <title>Cadastro de Coordenadas PTZ</title>
  <style>
    body { font-family: sans-serif; background: #f8f8f8; margin: 0; padding: 0; }
    .stream-container { display: flex; flex-direction: row; }
    #streamimg { width: 1280px; height: 720px; background: #000; display: block; object-fit: cover; }
    form { margin-left: 32px; }
    label { display: block; margin: 8px 0 2px 0; }
    input[readonly] { background: #eee; }
    button { margin: 6px 4px 6px 0; }
  </style>
</head>
<body>
  <h1 style="margin-left:12px;">Cadastro de Coordenadas PTZ</h1>
  <div class="stream-container">
    <img id="streamimg" src="{{ url_for('video_feed') }}">
    <form id="ptzForm">
      <label>ID da Câmera: <input name="cameras_id" id="cameras_id" required></label>
      <label>Descrição: <input name="desc" id="desc" required></label>
      <label>Pan: <input id="pan" name="pan" readonly></label>
      <label>Tilt: <input id="tilt" name="tilt" readonly></label>
      <label>Zoom: <input id="zoom" name="zoom" readonly></label>
      <div>
        <button type="button" onclick="PegarPTZ()">Obter PTZ</button>
        <button type="button" onclick="SalvarPTZ()">Salvar no Banco</button>
      </div>
      <div id="mensagem" style="margin-left:12px;margin-top:12px;"></div>
    </form>
  </div>
<script>
function PegarPTZ() {
  fetch('/pega_ptz', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({})
  })
  .then(resp=>resp.json())
  .then(res=>{
    const [p, t, z] = res.ptz_atualizado.split(',').map(v => v.trim());
    document.getElementById('pan').value = p;
    document.getElementById('tilt').value = t;
    document.getElementById('zoom').value = z;
    document.getElementById('result').innerText = "PTZ obtido com sucesso!";
  });
}

function SalvarPTZ() {
  const data = {
    cameras_id: document.getElementById('cameras_id').value,
    desc: document.getElementById('desc').value,
    pan: parseFloat(document.getElementById('pan').value),
    tilt: parseFloat(document.getElementById('tilt').value),
    zoom: parseFloat(document.getElementById('zoom').value),
  };
  fetch('/salvar_ptz', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(data)
  })
  .then(resp => resp.json())
  .then(res => {
    const divMsg = document.getElementById('mensagem');
    divMsg.textContent = res.message || 'Erro ao salvar!';
  })
  .catch(() => {
    const divMsg = document.getElementById('mensagem');
    divMsg.innerText = 'Erro de conexão.';
  });
}
</script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/pega_ptz', methods=['POST'])
def pega_ptz():
    ptz = CAM.get_ptz()
    tptz = f"{ptz[0]}, {ptz[1]}, {ptz[2]}"
    return jsonify({"ptz_atualizado": tptz})

@app.route('/salvar_ptz', methods=['POST'])
def salvar_ptz():
    data = request.json
    try:
        conn = mysql.connector.connect(
            host='10.0.5.103',
            user='AoFessaysBBKEnd',
            password='fRe_QHOud_Bwed.B',
            database='AoFessays'
        )
        cursor = conn.cursor()
        sql = """
        INSERT INTO coordenadas_cameras (`cameras_id`, `pan`, `tilt`, `zoom`, `desc`)
        VALUES (%s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (
            data['cameras_id'],
            data['pan'],
            data['tilt'],
            data['zoom'],
            data['desc']
        ))
        conn.commit()
        return jsonify({"message": "Dados salvos com sucesso!"})
    except Exception as e:
        return jsonify({"message": f"Erro ao salvar: {str(e)}"}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == '__main__':
    app.run(debug=True, port=5001, host="0.0.0.0")
