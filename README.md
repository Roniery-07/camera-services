# AoFNGD
AoF Nova Geração Distribuído

## Estrutura do diretório `src` (Classes, Objetos e Funções)

O diretório `src` concentra a lógica principal do projeto AoFNGD. Abaixo segue uma visão geral dos principais arquivos e o que normalmente você encontrará em termos de classes, objetos e funções:

- **AoFBase.py**: arquivo que abriga a classe abstrata base do projeto, AoFBase, comum para todos os tipos de câmera.
  - _get_frame(self)_: método utilizado para obter um snapshot JPEG diretamente do servidor ZoneMinder.
  - _zbx_metrics(self)_: método que permite a leitura das delemetrias de cada uma das threads através do Supervisor.py.
  - _save_all_analyzed_video(self, metadados, analyzed_frames, analyzed_frames_wbbox)_: método para salvamento dos arquivos de vídeo de detecções e dos respectivos metadados.
  - _draw_boxes(self, image, results)_: método para desenho das BBox em frames com detecção.
  - _send_frame_to_rabbit(self, frame_bytes, camera)_: método para envio de frames para processamento distribuído através do broker RabbitMQ.
  - _processar_bboxes(self, todos_results)_: método que retorna as estatísticas de todas as BBox obtidas durante os períodos de detecção.
  - _bbox_maior_score(self, todos_results)_: método que retorna a BBox de maior pontuação de um vetor de BBox.
  - _ajustar_ptz_: método (experimental) para cálculo de ajuste de PTZ com base na coordenada atual + BBox de detecção.
    
- **Cameras.py**: arquivo que abriga as subclasses, que herdam a classe AoFBase, para implementação dos controles específicos.
  - _class CameraPTZ_: subclasse abstrada base para todos os modelos de câmeras PTZ.
    - _def atualiza_dados_camera_ptz(self, config_sys, config_cam)_: método que retorna as configurações atualizadas de uma câmera PTZ com base no banco de dados.
    - _class CameraPTZMBosch_: subsubclasse para processamento de câmeras PTZ Bosch.
  - _class CameraFixa_: subclasse para processamento de câmeras fixas.
    - _def atualiza_dados_camera_fixa(self, config_sys, config_cam)_: método que retorna as configurações atualizadas de uma câmera fixa com base no banco de dados.

- **Utilidades.py**: arquivo que abriga funções que são utilizadas tanto dentro das classes quanto na aplicação Supervisor.py
  - _def buscar_cameras(config)_: retorna a lista de câmeras existentes no banco de dados.
  - _def configurar_logger(nome_camera, nome_logger="AoFLogger")_: inicializa sistema de logs baseado em filas para toda a aplicação.

- **Supervisor.py**: arquivo que é responsávelo pelo lançamento e monitoramento das threads de processamento de cada câmera. Sendo responsável ainda por coletar e enviar as telemetrias para o servidor Zabbix.

O diretório `services` contém serviços de auxílio:

- **aof_worker**: servidor de processamento distribuído de frames baseado no Broker RabbitMQ e YOLO8.

- **envia_notificacao**: serviço que observa o diretório de vídeos detectados e faz a gravação em banco de dados. Temporáriamente esse serviço também está enviando notificações via telegram em decorrência do período experimental.

- **file_server**: servidor de arquivos desenvolvido em JavaScript, sendo executado como um docker, que tem o objetivo de fornecer URLs seguras para acesso aos arquivos de vídeo detectados.

- **systemd**: scripts do systemd para execução dos serviços: Supervisor.py, aof-worker.py e envia_notificacao.py.

## Troubleshooting

- A biblioteca sensecam_control, em seu arquivo sensecam_control/vapix_control.py, configura uma metodologia própria de log com level=logging.DEBUG, isso gera um alto processamento e um grande arquivo de log. É necessário alterá-lo para level=logging.CRITICAL para mitigar os efeitos nocivos de log dessa biblioteca.
- O Zoom Digital da Câmera Bosch deve ser desativado para que a função de ajuste funcione corretamente. É possível que, como a função não está totalmente corrigida, ela seja dependente da calibração que foi realizada com o Zoom Digital desligado.
