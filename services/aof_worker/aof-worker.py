import pika
import time
import io
import cv2
from ultralytics import YOLO
import numpy as np
import msgpack
import json
import logging
import torch
from sdnotify import SystemdNotifier
import threading
from pyzabbix import ZabbixSender, ZabbixMetric

# Comunicação com whatchdog
notifier = SystemdNotifier()

# Configuração do Logger
logger = logging.getLogger("aof_worker")
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler('/var/log/aof_worker.log', mode='a')
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
logger.addHandler(file_handler)
logger.info(f"Inicializando worker de detecção...")

# Configuração do Modelo e Classes
MODEL_PATH = '/usr/local/src/AoFNG/services/aof_worker/AoFModels/dfire-yolov8-medium.pt'
CLASSES = ['Fumaca', 'Fogo']

# Configuração Zabbix
ZBX_SERVER = 'sentinela.cefala.org'
ZBX_PORT = 10051
ZBX_HOST_NAME = 'WorkersTFAI'
ZBX_WORKER_NAME = 'WoTFAI_CEFALA01'
ZBX_DISCOVERY_KEY = 'tfai.workers.discovery'
ZBX_FRAMES_KEY = f'tfai.worker.frames_processed[{ZBX_WORKER_NAME}]'
ZBX_AVG_TIME_KEY = f'tfai.worker.avg_process_time[{ZBX_WORKER_NAME}]'
ZBX_TELEMETRY_INTERVAL = 30

# Telemetria global
frames_processed = 0
total_process_time = 0.0
lock = threading.Lock()

# Carrega modelo YOLOv8 na GPU
model = YOLO(MODEL_PATH)
logger.info(f"CUDA disponível? {torch.cuda.is_available()}")
logger.info(f"Número de GPUs: {torch.cuda.device_count()}")
logger.info(f"Nome GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'Nenhuma'}")



credentials = pika.PlainCredentials('AoF', 'Issp2006!')
parameters = pika.ConnectionParameters(
    host='10.0.5.14',
    port=5672,
    virtual_host='/',
    credentials=credentials
)

connection = pika.BlockingConnection(parameters)
channel = connection.channel()

def on_request(ch, method, props, body):
    global frames_processed, total_process_time
    start_time = time.perf_counter()
    try:
        payload = msgpack.unpackb(body)
        bytes_img = payload['image']
        nparr = np.frombuffer(bytes_img, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        y_conf = payload['y_conf']
        y_iou = payload['y_iou']

        if frame is None:
            raise ValueError("Imagem inválida")

        results = model.predict(frame, conf=y_conf, iou=y_iou)[0]
        detectadas = [CLASSES[int(cls)] for cls in results.boxes.cls]

        boxes = results.boxes
        results_data = {
            'boxes': boxes.xyxy.tolist(),
            'scores': boxes.conf.tolist(),
            'classes': boxes.cls.tolist()
        }
        resposta = {
            'results': results_data,
            'detectadas': detectadas,
            'quantidade': len(detectadas)
        }

        ch.basic_publish(
            exchange='',
            routing_key=props.reply_to,
            properties=pika.BasicProperties(
                correlation_id=props.correlation_id
            ),
            body = msgpack.packb(resposta, use_bin_type=True)
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)

        # Atualiza telemetria
        elapsed = time.perf_counter()
        process_time = (elapsed - start_time)
        with lock:
            frames_processed += 1
            total_process_time += process_time
        logger.info(f"Frame processado e enviado em {process_time * 1000:.0f} ms com {len(boxes)} boxes.")

        # Notifica vida ao watchdog
        notifier.notify("WATCHDOG=1")

    except Exception as e:
        logger.error(f"[ERRO]: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag)

def send_telemetry():
    global frames_processed, total_process_time
    sender = ZabbixSender(zabbix_server=ZBX_SERVER, zabbix_port=ZBX_PORT)
    discovery_interval = 3600
    last_discovery = 0

    while True:
        try:
            time.sleep(ZBX_TELEMETRY_INTERVAL)
            with lock:
                now = time.time()
                if now - last_discovery > discovery_interval:
                    discovery_data = [{"{#WORKERNAME}": ZBX_WORKER_NAME}]
                    resp = sender.send([
                        ZabbixMetric(ZBX_HOST_NAME, ZBX_DISCOVERY_KEY, json.dumps({'data': discovery_data}))
                    ])
                    logger.info(f"Discovery enviado para Zabbix: {resp}")
                    last_discovery = now

                resp = sender.send([
                    ZabbixMetric(ZBX_HOST_NAME, ZBX_FRAMES_KEY, frames_processed),
                    ZabbixMetric(ZBX_HOST_NAME, ZBX_AVG_TIME_KEY,
                                total_process_time / frames_processed if frames_processed else 0.0)
                ])
                logger.info(f"Métricas enviadas para Zabbix: {resp}") 

                frames_processed = 0
                total_process_time = 0.0
        except Exception as e:
            logger.error(f"Erro ao enviar telemetria: {e}")
            time.sleep(5)

# Inicie a thread de telemetria
telemetry_thread = threading.Thread(target=send_telemetry, daemon=True)
telemetry_thread.start()

# Inicie o consumo no RabbitMQ
channel.basic_qos(prefetch_count=1)
channel.basic_consume(queue='aof_camera_frames', on_message_callback=on_request)

logger.info("Aguardando frames para análise...")
channel.start_consuming()
