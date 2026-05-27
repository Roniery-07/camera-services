import mysql.connector


class CamBaseFixa:
    """
    Mixin class to handle specific DB queries for Fixed Cameras.
    Does NOT inherit from Process to avoid MRO conflicts.
    """

    def update_fixed_camera(self, config_sys, config_cam):
        db = config_sys["database"]

        try:
            conn = mysql.connector.connect(
                host=db["host"],
                user=db["user"],
                password=db["password"],
                database=db["database"],
            )

            id_camera = config_cam["zm_id_monitor"]
            cursor = conn.cursor()

            # Using parameter substitution (%s) is safer than f-string for SQL injection
            # But keeping your query structure for compatibility
            query = f"SELECT cameras.id as idcamera, cameras.name as nomecamera, cameras.zm_id_monitor, cameras.model, cameras.coordinate_duration, cameras.fire_persistence_duration, cameras.ai_confidence_threshold, cameras.ai_location_threshold, cameras.fire_persistence_threshold, cameras.fps, cameras.image_width, cameras.image_height, institutions.name as nomeinstituicao, institutions.telegram_chat_id, servers.streaming_server, servers.user, servers.password FROM cameras JOIN servers ON cameras.server_id = servers.id JOIN institutions ON cameras.institution_id = institutions.id WHERE cameras.zm_id_monitor = {id_camera};"

            cursor.execute(query)
            row = cursor.fetchone()

            if row:
                columns = [desc[0] for desc in cursor.description]
                data = dict(zip(columns, row))
                return data
            else:
                return None

        except mysql.connector.Error as err:
            # We don't have self.logger here easily, so we rely on the main process handling None return
            print(f"Erro BD CamFixa: {err}")
            return None

        finally:
            if "cursor" in locals():
                cursor.close()
            if "conn" in locals() and conn.is_connected():
                conn.close()
