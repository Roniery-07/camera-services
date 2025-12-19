# Logging para registrar status e erros
import logging
# Cliente do Telegram para interações básicas (não é usado diretamente em todos os envios)
from telegram import Bot
# Requests para chamadas HTTP às APIs externas
import requests

# Endpoint de login do backend (não utilizado diretamente neste arquivo)
LOGIN_URL = "https://aofessays.bkend.apagaofogo.eco.br/public/login"
# Template de URL para download de detecções por ID (usado em baixar_video)
DOWNLOAD_URL_TEMPLATE = "https://aofessays.bkend.apagaofogo.eco.br/private/detections/{id}/download"

# Credenciais de acesso para o backend (se necessário em outros fluxos)
CREDENCIAIS = {
    "email": "usuario_notificacoes@gmail.com",
    "senha": "123@123"
}

#! Tokens de Telegram (atenção: estes valores devem ser trocados em produção)
TOKEN_TELEGRAM = '8430698995:AAFAnvSdl_jZ_jCLGsYwpXhDwxI4KnxXlSY'
TOKEN_TELEGRAM_TESTES = '8308992029:AAHKzOeVh9Av-rkPBgxAHCDyTyrBEPCYm6c'
# Chat padrão de destino para as mensagens
CHAT_ID_TELEGRAM = '-4956856492'
# Instancia um Bot do Telegram (não utilizado nas funções que usam requests)
BOT = Bot(token=TOKEN_TELEGRAM)

# Configuração de arquivos de log
log_file = "notifications.log"
results_file = "results.log"

# Loggers: resultados e log geral
results = logging.getLogger(results_file)
results.setLevel(logging.INFO) 
log = logging.getLogger(log_file)
log.setLevel(logging.INFO)

# Handlers de arquivo
file_handler_results = logging.FileHandler(results_file)
file_handler_log = logging.FileHandler(log_file)

# Formatação de mensagens de log
formatter = logging.Formatter(
                            '[%(asctime)s] - %(levelname)s - %(message)s',
                            datefmt='%d-%b-%Y %H:%M:%S'
                            )

# Aplica o formatter aos handlers
file_handler_results.setFormatter(formatter)
file_handler_log.setFormatter(formatter)

# Associa handlers aos loggers
results.addHandler(file_handler_results)
log.addHandler(file_handler_log)

def baixar_video(detection_id, token):
    """
    Faz uma requisição autenticada (Bearer token) para baixar o vídeo
    relacionado a uma detecção específica.
    """
    # Monta a URL final para download de vídeo da detecção fornecida
    url_download = DOWNLOAD_URL_TEMPLATE.format(id=detection_id)
    
    # Cabeçalhos com Bearer token para autenticação
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    # Registra início do processo
    log.info(f"\nPasso 2: Baixando o vídeo da detecção ID: {detection_id}...")
    try:
        # Requisição GET com stream=True para baixar em chunks (evita usar muita memória)
        response = requests.get(url_download, headers=headers, stream=True)
        # Lança exceção se status HTTP não for sucesso
        response.raise_for_status()

        # Define o nome de arquivo onde será salvo o vídeo
        nome_arquivo = f"deteccao_{detection_id}_com_bbox.mp4"
            
        # Escreve o conteúdo do vídeo em disco em blocos de 8KB
        with open(nome_arquivo, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Loga sucesso e retorna o caminho do arquivo salvo
        log.info(f">>> Sucesso! O vídeo foi salvo como '{nome_arquivo}'")
        return nome_arquivo

    # Trata erros HTTP (status code e corpo), registrando detalhes
    except requests.exceptions.HTTPError as err:
        log.info(f"Erro ao baixar o vídeo: Status {err.response.status_code} - {err.response.text}")
    # Trata erros de conexão e outros problemas de requests
    except requests.exceptions.RequestException as e:
        log.info(f"Erro de conexão ao baixar o vídeo: {e}")
# Adicione esta nova função ao seu arquivo platform_functions.py

def enviar_mensagem_simples_telegram(mensagem, chatid):
    """
    Envia uma mensagem de texto simples para um chat do Telegram usando o método sendMessage.
    Útil para alertas e mensagens de inicialização/saúde do serviço.
    """
    # Endpoint do método sendMessage da API do Telegram
    url = f"https://api.telegram.org/bot{TOKEN_TELEGRAM}/sendMessage"
    # Payload com parse_mode=Markdown para permitir formatação básica
    payload = {
        'chat_id': chatid,
        'text': mensagem,
        'parse_mode': 'Markdown'
    }
    try:
        # Envia a requisição POST e aguarda até 10 segundos
        response = requests.post(url, json=payload, timeout=10)
        # Verifica se a resposta é HTTP 2xx
        response.raise_for_status()
        # Registra sucesso
        log.info(f"Mensagem de status enviada para o chat ID {chatid}.")
        return True
    except requests.exceptions.RequestException as e:
        # Registra o erro e, se houver, o corpo de resposta da API do Telegram
        log.error(f"Falha ao enviar mensagem de status para o Telegram: {e}")
        if e.response:
            log.error(f"Detalhe do erro da API: {e.response.text}")
        return False

def telegram_notification(message, image_url, chatid):
    """
    Envia uma notificação com imagem (thumbnail) para o Telegram via sendPhoto.
    'image_url' pode ser uma URL pública ou uma URL assinada temporária.
    """
    # Endpoint do método sendPhoto
    url = f"https://api.telegram.org/bot{TOKEN_TELEGRAM}/sendPhoto"
    # Corpo com chat, legenda (caption) e URL da imagem
    data = {
        "chat_id": chatid,
        "caption": message,
        "photo": image_url
    }
    try:
        # Requisição POST com timeout generoso (30s)
        response = requests.post(url, json=data, timeout=30)
        # Verifica sucesso (2xx)
        response.raise_for_status()
        # Loga que a imagem foi "enviada" (via URL) e retorna um status simplificado
        log.info(f"Imagem enviado para o Telegram: {image_url}")
        return {'status': 'success'}
    except requests.exceptions.RequestException as e:
        # Em caso de falha, registra erro e retorna status com detalhe
        log.error(f"Erro ao enviar imagem para o Telegram: {e}")
        return {'status': 'error_send', 'error': str(e)}

def enviar_notificacao_telegram(detection_entry, video, chatid = CHAT_ID_TELEGRAM):
    """
    Constrói a mensagem com dados da detecção e envia para o Telegram
    usando telegram_notification (que usa sendPhoto).
    """
    # Monta a mensagem com nome da câmera e data/hora da detecção
    mensagem = f"Nova detecção: {detection_entry['nomecamera']} em {detection_entry['datahora']}.\nPara mais detahes, consulte: https://aofessays.ftend.apagaofogo.eco.br/events"
    # Envia a notificação (neste caso, 'video' é a URL da imagem/thumbnail assinada)
    result = telegram_notification(mensagem, video, chatid)
    
    # Constrói um feedback resumido do envio
    feedback = {
        'id': detection_entry['id'],
        'mensagem': mensagem,
        'status': result.get('status'),
        'error': result.get('error')
    }
    
    # Retorna o feedback para controle de sucesso/erro
    return feedback

def enviar_notificacao_push(detection_entry):
    """
    Envia uma notificação push via endpoint específico do backend.
    Útil para integrações com aplicativos/serviços que consomem o push.
    """
    # Endpoint responsável por disparar o push
    url = "https://aofessays.bkend.apagaofogo.eco.br/push/dispatch"
    
    # Corpo mínimo com ID do evento e mensagem
    body = {
        "eventId": int(detection_entry['id']),
        "message": "Nova detecção: {} em {}".format(detection_entry['id'], detection_entry['datahora_abertura']),
    }
    
    # Estrutura básica de feedback
    feedback = {'id': int(detection_entry['id']), 'status': 'unknown', 'error': None}
    
    try:
        # Faz o POST com JSON e timeout de 30s
        response = requests.post(url, json=body, timeout=30)
        # Levanta exceção se houver falha HTTP
        response.raise_for_status()
        
        # Atualiza o feedback para sucesso
        feedback['status'] = 'success'
        log.info(f"Notificação push para o ID '{detection_entry['id']}' enviada com sucesso!")
        
    except requests.exceptions.RequestException as e:
        # Em caso de falha, marca erro e registra detalhes
        feedback['status'] = 'error'
        feedback['error'] = str(e)
        log.error(f"Erro ao enviar notificação push para o ID '{detection_entry['id']}': {e}")
        
    # Retorna o status final do envio
    return feedback