import json
import time
import os
import logging
from pyzabbix import ZabbixMetric, ZabbixSender
from sdnotify import SystemdNotifier
from utils.cameras import get_cameras

# Import the NEW Process-based classes
from cameras.CamFixa import CameraFixa
from utils.logger import setup_root_logger


# --- 2. FACTORY FUNCTION ---
def create_cam_process(cam, config_sys):
    """
    Creates the Process object based on the camera model.
    """
    logger_name = f"Cam.{cam['nomecamera']}"

    # Models 2 and 3 are the FFMPEG/Multiprocessing ones
    if cam["model"] == 2:
        return CameraFixa(
            config_sys=config_sys, config_cam=cam, logger_name=logger_name
        )

    elif cam["model"] == 3:
        # return CameraPTZMBoschFFMPEG(config_sys=config_sys, config_cam=cam, logger_name=logger_name)
        pass

    return None


def main():
    # 1. Setup Logging First
    setup_root_logger()
    logger = logging.getLogger("Supervisor")
    logger.info("Initializing System...")

    # 2. Load Config
    try:
        logger.info(os.listdir())
        with open("./src/config.json", "r") as f:
            config_sys = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load config.json: {e}")
        return

    # 3. Systemd Watchdog
    notifier = SystemdNotifier()

    # 4. Get Cameras from DB
    cameras = get_cameras(config_sys)

    # Dictionary to hold running processes: { "CameraName": (ProcessObj, ConfigDict) }
    processos = {}

    # 5. Start Processes
    for cam in cameras:
        try:
            p = create_cam_process(cam, config_sys)
            if p:
                p.start()
                processos[cam["nomecamera"]] = (p, cam)
                logger.info(f"Process started for {cam['nomecamera']} (PID: {p.pid})")
            else:
                logger.warning(f"Model {cam['model']} not implemented yet or skipped.")
        except Exception as e:
            logger.error(f"Failed to start process for {cam['nomecamera']}: {e}")

    # Zabbix Sender
    zabbix = ZabbixSender(zabbix_server=config_sys["zabbix"]["zbx_server_ip"])

    # Loop Settings
    T_SUPERVISOR = 60

    try:
        while True:
            # Restored the loop. If you sleep 60s straight, Systemd might kill this service.
            # We sleep in 1s chunks to keep the Watchdog happy.
            for _ in range(T_SUPERVISOR):
                time.sleep(1)
                notifier.notify("WATCHDOG=1")

            # --- MONITORING LOOP ---
            zbx_metricas = []
            zbx_discovery = []
            total_quadros_processados = 0
            total_quadros_detectados = 0
            total_erros_threads = 0

            # Create a list copy to avoid Runtime error if dictionary changes during iteration
            for nome, (p, cam) in list(processos.items()):
                # A. Check Health
                if not p.is_alive():
                    logger.error(
                        f"Process for {nome} died (Exit Code: {p.exitcode}). Restarting..."
                    )
                    total_erros_threads += 1

                    # Cleanup Zombie Process
                    p.join()

                    try:
                        new_p = create_cam_process(cam, config_sys)
                        if new_p:
                            new_p.start()
                            processos[nome] = (new_p, cam)
                            logger.info(f"Restarted {nome} with PID {new_p.pid}")
                        continue  # Skip metrics for this dead cycle
                    except Exception as e:
                        logger.error(f"Failed to restart {nome}: {e}")
                        continue

                # B. Collect Metrics
                try:
                    q_proc = p.quadros_processados.value
                    q_det = p.quadros_detectados.value

                    total_quadros_processados += q_proc
                    total_quadros_detectados += q_det

                    zbx_metricas.append(
                        ZabbixMetric(
                            config_sys["zabbix"]["zbx_hostname"],
                            f"aofngd.quadros_processados[{nome}]",
                            q_proc,
                        )
                    )
                    zbx_metricas.append(
                        ZabbixMetric(
                            config_sys["zabbix"]["zbx_hostname"],
                            f"aofngd.quadros_detectados[{nome}]",
                            q_det,
                        )
                    )
                    zbx_discovery.append({"{#CAMERA}": nome})
                except Exception as e:
                    logger.error(f"Metric collection failed for {nome}: {e}")

            # --- ZABBIX SENDING ---
            try:
                # Discovery
                if zbx_discovery:
                    zbx_discovery_data = [
                        ZabbixMetric(
                            config_sys["zabbix"]["zbx_hostname"],
                            "aofngd.discovery",
                            json.dumps({"data": zbx_discovery}),
                        )
                    ]
                    zabbix.send(zbx_discovery_data)

                # Metrics
                if zbx_metricas:
                    zbx_metricas.append(
                        ZabbixMetric(
                            config_sys["zabbix"]["zbx_hostname"],
                            "aofngd.total_quadros_processados",
                            total_quadros_processados,
                        )
                    )
                    zbx_metricas.append(
                        ZabbixMetric(
                            config_sys["zabbix"]["zbx_hostname"],
                            "aofngd.total_quadros_detectados",
                            total_quadros_detectados,
                        )
                    )
                    zbx_metricas.append(
                        ZabbixMetric(
                            config_sys["zabbix"]["zbx_hostname"],
                            "aofngd.total_erros_threads",
                            total_erros_threads,
                        )
                    )

                    result = zabbix.send(zbx_metricas)
                    logger.info(f"Zabbix Sent: {result}")

            except Exception as e:
                logger.error(f"Zabbix Send Failed: {e}")

    except KeyboardInterrupt:
        logger.info("Stopping Supervisor...")
        # Graceful Shutdown
        for nome, (p, _) in processos.items():
            logger.info(f"Stopping {nome}...")
            p.stop()
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
        logger.info("Bye.")


if __name__ == "__main__":
    main()
