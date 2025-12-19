import requests
from requests.auth import HTTPDigestAuth
import xml.etree.ElementTree as ET
import time

# Dados da câmera POSTO39 PESRM
ip_camera = '192.168.2.123'
usuario = 'admin'
senha = 'Abc12345'

# Namespace utilizado no XML
ns = {'hik': 'http://www.hikvision.com/ver20/XMLSchema'}
# Endpoint ISAPI
url = f'http://{ip_camera}/ISAPI/PTZCtrl/channels/1/status'
# Contador de segundos
segundos = 0
# Cria a sessão HTTP uma única vez
session = requests.Session()

try:
    while True:
        response = session.get(url, auth=HTTPDigestAuth(usuario, senha))

        if response.status_code == 200:
            xml_root = ET.fromstring(response.text)

            elevation = xml_root.findtext('.//hik:elevation', namespaces=ns)
            azimuth = xml_root.findtext('.//hik:azimuth', namespaces=ns)
            absolute_zoom = xml_root.findtext('.//hik:absoluteZoom', namespaces=ns)

            print(f"[{segundos:03}s] Pan: {azimuth}, Tilt: {elevation}, Zoom: {absolute_zoom}")
        else:
            print(f"[{segundos:03}s] Erro ao obter status (HTTP {response.status_code})")

        segundos += 1
        time.sleep(1)

except KeyboardInterrupt:
    print("\nMonitoramento encerrado.")

finally:
    session.close()
