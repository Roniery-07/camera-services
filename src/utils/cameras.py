import mysql.connector


def get_cameras(config):
    db = config["database"]

    print(db)
    try:
        conn = mysql.connector.connect(
            host=db["host"],
            user=db["user"],
            password=db["password"],
            database=db["database"],
        )
        cameras = config["cameras"]["zm_monitor_id"]
        ids_cameras = ",".join(map(str, cameras))

        cursor = conn.cursor()
        cursor.execute(
            f"SELECT cameras.id as idcamera, cameras.name as nomecamera, cameras.zm_id_monitor, cameras.model, cameras.ip, cameras.user, cameras.password, cameras.delay_between_stream_and_control, cameras.target_active_control, cameras.coordinate_duration, cameras.fire_persistence_duration, cameras.ai_confidence_threshold, cameras.ai_location_threshold, cameras.fire_persistence_threshold, cameras.fps, cameras.image_width, cameras.image_height, institutions.name as nomeinstituicao, institutions.telegram_chat_id, servers.streaming_server, servers.user, servers.password FROM cameras JOIN servers ON cameras.server_id = servers.id JOIN institutions ON cameras.institution_id = institutions.id WHERE cameras.zm_id_monitor IN ({ids_cameras});"
        )

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        resultados = []
        for row in rows:
            data = dict(zip(columns, row))
            resultados.append(data)

        return resultados

    except mysql.connector.Error as err:
        print(f"Erro ao conectar ou executar a consulta: {err}")
        return []

    finally:
        if "cursor" in locals():
            cursor.close()
        if "conn" in locals() and conn.is_connected():
            conn.close()
