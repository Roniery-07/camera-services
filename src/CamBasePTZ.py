from abc import ABC, abstractmethod
from AoFBase import AoFBase
import mysql.connector
import json
import time
import math

# Subclasse Genérica de Câmeras PTZ
class CamBasePTZ(AoFBase):
    @abstractmethod
    def run(self):
        pass

    def atualiza_dados_camera_ptz(self, config_sys, config_cam):
        db = config_sys["database"]

        try:
            conn = mysql.connector.connect(
                host=db["host"],
                user=db["user"],
                password=db["password"],
                database=db["database"]
            )

            id_camera = config_cam["zm_id_monitor"]
            cursor = conn.cursor()
            cursor.execute(f"SELECT cameras.id as idcamera, cameras.nome as nomecamera, cameras.zm_id_monitor, cameras.ptz_ip, cameras.ptz_user, cameras.ptz_password, cameras.ptz_delay_stream, cameras.ptz_ctrl_ativo, cameras.duracaobusca, cameras.duracaoconfirmacao, cameras.y_conf, cameras.y_iou, cameras.perc_frames_para_mp4, cameras.fps, cameras.width, cameras.height, instituicoes.nome as nomeinstituicao, instituicoes.chat_id_telegram, zm_servers.url, zm_servers.usuario, zm_servers.senha, JSON_ARRAYAGG(JSON_ARRAY(coordenadas_cameras.id, ROUND(coordenadas_cameras.pan, 3), ROUND(coordenadas_cameras.tilt, 3), ROUND(coordenadas_cameras.zoom, 3), coordenadas_cameras.desc)) AS coordenadas FROM coordenadas_cameras JOIN cameras ON coordenadas_cameras.cameras_id = cameras.id JOIN zm_servers ON cameras.zm_servers_id = zm_servers.id JOIN instituicoes ON cameras.instituicoes_id = instituicoes.id WHERE cameras.zm_id_monitor = {id_camera} AND coordenadas_cameras.active = 1 GROUP BY cameras.id, cameras.nome, cameras.zm_id_monitor, cameras.ptz_ip, cameras.ptz_user, cameras.ptz_password, cameras.ptz_delay_stream, cameras.ptz_ctrl_ativo, cameras.duracaobusca, cameras.duracaoconfirmacao, cameras.y_conf, cameras.y_iou, cameras.perc_frames_para_mp4, cameras.fps, cameras.width, cameras.height, instituicoes.nome, instituicoes.chat_id_telegram, zm_servers.url, zm_servers.usuario, zm_servers.senha")

            row = cursor.fetchone()
            if row:
                columns = [desc[0] for desc in cursor.description]
                data = dict(zip(columns, row))
                if data.get("coordenadas"):
                    data["coordenadas"] = json.loads(data["coordenadas"])
                return data
            else:
                return None

        except mysql.connector.Error as err:
            print(f"Erro ao conectar ou executar a consulta: {err}")
            return None

        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals() and conn.is_connected():
                conn.close()

    def ptz_tracking(self,
        w,h,a,b,c,
        Dpi,Dti,z,zomax,eta=3,
        alpha=None,Dpo=None,Dto=None,Dp=None,Dt=None
    ):
        ''' Compute pan-tilt-zoom displacement to center object.

        Args:
            w (float): image width [pixels]
            h (float): image height [pixels]
            a (float): object width [pixels]
            b (float): object height [pixels]
            c (2-tuple of float): object center horizontal and vertical coordinates [pixels]
            Dpi (float): image camera space pan
            Dti (float): image camera space tilt
            z (float): current camera space zoom
            zomax (float): image maximum camera zoom coordinate
            eta (float, optional): target image fraction size reference (default is 3)
            alpha (2-tuple of float, optional): camera horizontal and vertical field of view [degrees] (default is None)
            Dpo (float, optional): maximum pan displacement (default is None) [degrees]
            Dto (float, optional): maximum tilt displacement (default is None) [degrees]
            Dp (float, optional): maximum camera space pan displacement (default is None)
            Dt (float, optional): maximum camera space tilt displacement (default is None)

        Returns:
            (float): camera space pan displacement
            (float): camera space tilt displacement
            (float): camera space zoom displacement
        '''

        # Pan-tilt limits from field of view.
        if Dpi is None or Dti is None:
            Dpi = alpha[0] * Dp / Dpo
            Dti = alpha[1] * Dt / Dto

        # Compute camera space displacements to center object.
        dp = (1/2 - c[0]/w) * Dpi
        dt = (1/2 - c[1]/h) * Dti
        dz = min(1, max(0, z + math.log(min(w/eta/a, h/eta/b), zomax))) - z

        return dp, dt, dz

    def sensor_size(self,L,W,H):
        D = math.sqrt(W*W + H*H) # maximum image diagonal length [pixels]
        return L*W/D, L*H/D # sensor horizontal and vertical lengths

    def fov(self,zomax,z,wmax,hmax,W,H,L,F):
        """
        Returns the field of view (FOV) angles for a given set of parameters.

        This function determines the horizontal and vertical field of view angles
        based on the maximum depth, current depth exponent, object dimensions,
        and display characteristics. The calculation is based on geometric optics
        and screen-related parameters.

        Args:
            zomax (float): maximum camera zoom factor
            z (float): current camera space zoom
            wmax (float): maximum camera image width [pixels]
            hmax (float): maximum camera image height [pixels]
            W (int): sensor image width [pixels]
            H (int): sensor image height [pixels]
            L (float): sensor diagonal length [mm]
            F (float): focal length [mm]

        Returns:
            2-tuple of float: a 2-tuple containing the respective horizontal and
                            vertical field of view [degrees] at unit zoom factor.
        """

        # Field of view [degrees].
        Lw,Lh = self.sensor_size(L,wmax,hmax)
        return 2*math.degrees(math.atan(Lw/(2*F*zomax**z))), 2*math.degrees(math.atan(Lh/(2*F*zomax**z)))

    def move_camera_to_track_object(self, bbox,ptz,w,h,wmax,hmax,W,H,L,F,zomax,polim,tolim,plim,tlim,eta=3):
        ''' Returns camera relative move to track an object.

        Args:
            bbox (4-tuple of int): object bounding box [pixels]
            ptz (3-tuple of floats): current camera space pan, tilt and zoom
            w (float): camera image width [pixels]
            h (float): camera image height [pixels]
            wmax (float): maximum camera image width [pixels]
            hmax (float): maximum camera image height [pixels]
            W (int): sensor image width [pixels]
            H (int): sensor image height [pixels]
            L (float): sensor diagonal length [mm]
            F (float): focal length [mm]
            zomax (float): maximum camera zoom factor
            polim (float): camera physical pan limits [degrees]
            tolim (float): camera physical tilt limits [degrees]
            plim (float): camera space pan limits
            tlim (float): camera space tilt limits
            eta (float, optional): target image fraction size reference (default is 3)
        '''

        # Get current camera parameters.
        p,t,z = ptz
        Dpo = polim[1]-polim[0]
        Dto = tolim[1]-tolim[0]
        Dp = plim[1]-plim[0]
        Dt = tlim[1]-tlim[0]
        Dpi = None
        Dti = None
        alpha = self.fov(zomax,z,wmax,hmax,W,H,L,F)

        # Get bounding box size and center.
        a = bbox[2]-bbox[0] # bounding box width [pixels]
        b = bbox[3]-bbox[1] # bounding box height [pixels]
        c = ((bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2) # bounding box center [pixels]
        
        # Calculate relative pan-tilt-zoom displacements.
        dp,dt,dz = self.ptz_tracking(w,h,a,b,c,
            Dpi,Dti,z,zomax,eta,alpha,Dpo,Dto,Dp,Dt)

        # Set camera pan-tilt-zoom displacement command.
        return p-dp, t+dt, z+dz
