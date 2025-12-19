import json
from Utilidades import buscar_cameras, configurar_logger
import time
from CamFixa import CameraFixa
from CamPTZBosch import CameraPTZMBosch
from CamPTZMoveDetect import CamPTZMoveDetect
from pyzabbix import ZabbixMetric, ZabbixSender
from sdnotify import SystemdNotifier
# Teste com versão FFMPEG
from FFMPEGv.CamFixaFFMPEG import CameraFixaFFMPEG
from FFMPEGv.CamPTZBoschFFMPEG import CameraPTZMBoschFFMPEG

# Carrega a configuração
with open("config.json", "r") as f:
    config_sys = json.load(f)
    config_cam = "{}"

# Logger do sistema principal
logger = configurar_logger("supervisor")

# Comunicação com whatchdog
notifier = SystemdNotifier()

# Lista de Câmeras
cameras = buscar_cameras(config_sys)

# Lista de threads
threads = {}

# Cria thread conforme modelo (0-Fixa, 1-PtzBosch, 2-Fixa(FFMPEG), 3-PtzBosch(FFMPEG), 4-PtzDetecçãoMovimento)
def criar_thread(cam):
    if cam["modelo"] == 0:
        return CameraFixa(config_sys=config_sys, config_cam=cam, nome=cam["nomecamera"])
    elif cam["modelo"] == 1:
        return CameraPTZMBosch(config_sys=config_sys, config_cam=cam, nome=cam["nomecamera"])
    elif cam["modelo"] == 2:
        return CameraFixaFFMPEG(config_sys=config_sys, config_cam=cam, nome=cam["nomecamera"])
    elif cam["modelo"] == 3:
        return CameraPTZMBoschFFMPEG(config_sys=config_sys, config_cam=cam, nome=cam["nomecamera"])
    elif cam["modelo"] == 4:
        return CamPTZMoveDetect(config_sys=config_sys, config_cam=cam, nome=cam["nomecamera"])

# Inicia as threads
for cam in cameras:
    t = criar_thread(cam)
    t.start()
    threads[cam["nomecamera"]] = (t, cam)

# Objeto Zabbix Sender
zabbix = ZabbixSender(zabbix_server = config_sys["zabbix"]["zbx_server_ip"])

# Tempo do ciclo do supervisor
T_SUPERVISOR = 60

# Loop de supervisão das threads
while True:
    # Métricas e Discovery Zabbix
    zbx_metricas = []
    zbx_discovery = []
    # Totalização de métricas de quadros processados e detectados
    total_quadros_processados = 0
    total_quadros_detectados = 0
    total_erros_threads = 0

    # Tempo de espera para verificação de threads e envio de dados ao Zabbix
    time.sleep(T_SUPERVISOR)
    for nome, (t, cam) in list(threads.items()):
        if not t.is_alive():
            logger.error(f"Thread da câmera {nome} caiu. Reiniciando...")
            total_erros_threads += 1
            try:
                nova_thread = criar_thread(cam)
                nova_thread.start()
                threads[nome] = (nova_thread, cam)
            except Exception as e:
                logger.error(f"Falha ao reiniciar a thread da câmera {nome}: {e}")

        # A cada 30 segundos obtém os dados de quadros para envio ao zabbix (considerando sleep 5)
        try:
            quadros_processados, quadros_detectados = t.zbx_metrics()
            total_quadros_processados += quadros_processados
            total_quadros_detectados += quadros_detectados
            zbx_metricas.append(ZabbixMetric(config_sys["zabbix"]["zbx_hostname"], f"aofngd.quadros_processados[{nome}]", quadros_processados))
            zbx_metricas.append(ZabbixMetric(config_sys["zabbix"]["zbx_hostname"], f"aofngd.quadros_detectados[{nome}]", quadros_detectados))

            zbx_discovery.append({"{#CAMERA}": nome})
        except Exception as e:
            logger.error(f"Falha ao obter métricas de quadros processados e detectados: {e}")

    # Envio das métrias para o Zabbix
    try: 
        # Envio do Discovery
        zbx_discovery_data = []
        zbx_discovery_data.append(ZabbixMetric(config_sys["zabbix"]["zbx_hostname"], "aofngd.discovery", json.dumps({"data": zbx_discovery})))
        result_discovery = zabbix.send(zbx_discovery_data)
        # Envio de Métricas
        zbx_metricas.append(ZabbixMetric(config_sys["zabbix"]["zbx_hostname"], "aofngd.total_quadros_processados", total_quadros_processados))
        zbx_metricas.append(ZabbixMetric(config_sys["zabbix"]["zbx_hostname"], "aofngd.total_quadros_detectados", total_quadros_detectados))
        zbx_metricas.append(ZabbixMetric(config_sys["zabbix"]["zbx_hostname"], "aofngd.total_erros_threads", total_erros_threads))
        result_metricas = zabbix.send(zbx_metricas)
        logger.info(f"Zabbix Métricas: {result_metricas}")
    except Exception as e:
        logger.error(f"Falha ao enviar métricas para o Zabbix: {e}")

    # Notifica vida a watchdog
    notifier.notify("WATCHDOG=1")
