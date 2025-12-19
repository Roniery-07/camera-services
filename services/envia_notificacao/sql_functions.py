# Pandas para leitura de tabelas via conexão SQL e manipulação tabular
import pandas as pd
# socket para checar conectividade a hosts/ports (usado no watchdog de conectividade)
import socket
# logging para registrar operações e erros
import logging
# json para parse de campos JSON da base
import json
# hmac/hashlib para geração de assinatura (HMAC-SHA256) em URLs assinadas
import hmac
import hashlib
# urllib.parse para escaparmos corretamente paths em URLs
import urllib.parse
# time para timestamps/expiração de URLs assinadas
import time

# Chave secreta usada para assinar URLs de vídeo (HMAC)
VIDEO_SECRET_KEY = "dfWLXc6rXNtbfsbpFnb16f3MEhggPJ5thQoL8wunKPuPvKxiMv7LNFqoWHRRe1Iu022WjYvp63sbTqLozpsyyFAn0VVxec80iHI8pu5g7QxTKLbPPahpggrcaY11S1WD"

# Arquivos de log
log_file = "notifications.log"
results_file = "results.log"

# Loggers e níveis
results = logging.getLogger(results_file)
results.setLevel(logging.INFO) 
log = logging.getLogger(log_file)
log.setLevel(logging.INFO)

# Handlers de arquivo
file_handler_results = logging.FileHandler(results_file)
file_handler_log = logging.FileHandler(log_file)

# Formatação dos logs
formatter = logging.Formatter(
                            '[%(asctime)s] - %(levelname)s - %(message)s',
                            datefmt='%d-%b-%Y %H:%M:%S'
                            )

# Aplica formatadores aos handlers
file_handler_results.setFormatter(formatter)
file_handler_log.setFormatter(formatter)

# Adiciona handlers aos loggers
results.addHandler(file_handler_results)
log.addHandler(file_handler_log)

def read_database(conexao):
    """
    Lê as tabelas principais necessárias para o fluxo:
      - deteccoes (selecionando colunas específicas e extraindo 'nomecamera' de 'metadados' JSON)
      - eventos
      - deteccoes_eventos
      - zm_servers
    Retorna 4 DataFrames nessa ordem.
    """
    try:
        # Consulta apenas as colunas usadas do 'deteccoes' para eficiência
        query_deteccoes = "SELECT id, datahora, mp4, thumbnail, metadados, processamentos_id FROM deteccoes;"
        tabela_deteccoes_pd = pd.read_sql(query_deteccoes, conexao)

        # Extrai 'nomecamera' do JSON na coluna 'metadados'
        tabela_deteccoes_pd['nomecamera'] = tabela_deteccoes_pd['metadados'].apply(lambda x: json.loads(x).get('nomecamera'))
        # Remove a coluna 'metadados' para manter o DataFrame mais limpo
        tabela_deteccoes_pd = tabela_deteccoes_pd.drop(columns=['metadados'])
        
        # Lê a tabela de eventos completa
        query_eventos = "SELECT * FROM eventos;"
        tabela_eventos_pd = pd.read_sql(query_eventos, conexao)

        # Lê a tabela de links detecções<->eventos
        query_deteccoes_eventos = "SELECT * FROM deteccoes_eventos;"
        tabela_deteccoes_eventos_pd = pd.read_sql(query_deteccoes_eventos, conexao)

        # Lê a tabela de servidores (necessário para base_url dos arquivos)
        query_zm_servers = "SELECT * FROM zm_servers;"
        tabela_zm_servers_pd = pd.read_sql(query_zm_servers, conexao)

        # Retorna os 4 DataFrames lidos
        return tabela_deteccoes_pd, tabela_eventos_pd, tabela_deteccoes_eventos_pd, tabela_zm_servers_pd

    except Exception as err:
        # Em caso de falha, loga e retorna uma tupla reduzida indicando erro
        results.info(f"Erro ao ler do banco de dados: {err}")
        return None, None, None

def check_online_status():
    """
    Verifica conectividade com a Internet tentando conectar em portas DNS conhecidas.
    Retorna True na primeira conexão bem-sucedida, ou False após tentar todos.
    """
    servers = [
        ("1.1.1.1", 53),            # Cloudflare DNS
        ("208.67.222.222", 53),     # OpenDNS
        ("8.8.8.8", 53),            # Google DNS
    ]
    # Tenta conectar a cada servidor/porta com timeout de 5s
    for ip, port in servers:
        try:
            socket.create_connection((ip, port), timeout=5)
            return True
        except OSError:
            # Em caso de erro, tenta o próximo
            continue
    # Nenhuma conexão possível
    return False

def generate_signed_url(base_url:str, filename: str, expires_in_seconds: int = 14400, secret: str = VIDEO_SECRET_KEY) -> str:
    """
    Gera uma URL assinada temporária para acesso seguro a arquivos.
    Parâmetros:
      - base_url: domínio/base do file server (ex.: https://files.example.com)
      - filename: caminho do arquivo (ex.: 'folder/file.mp4')
      - expires_in_seconds: validade da URL (padrão 4 horas = 14400s)
      - secret: chave HMAC usada na assinatura (padrão VIDEO_SECRET_KEY)
    """
    # Garante que base_url e secret foram informados
    if not base_url or not secret:
        raise ValueError("As variáveis de ambiente VIDEO_BASE_URL e VIDEO_SECRET_KEY são obrigatórias.")

    # Timestamp de expiração
    expires = int(time.time()) + expires_in_seconds

    # Monta a string a ser assinada: "filename:expires"
    data = f"{filename}:{expires}"

    # Gera assinatura HMAC-SHA256 em hexdigest
    signature = hmac.new(
        secret.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    # Escapa segmentos do caminho para uma URL segura (evita espaços/caracteres especiais)
    safe_path = "/".join(urllib.parse.quote(part) for part in filename.split("/"))

    # Monta a URL final com query params 'expires' e 'signature'
    signed_url = f"{base_url}/file/{safe_path}?expires={expires}&signature={signature}"
    return signed_url

def get_chatid_by_camera_name(detecction, conexao) -> str:
    """
    Dado o registro de detecção, obtém o chat_id do Telegram associado à instituição da câmera.
    Passos:
      1) Consulta 'cameras' pelo nome para obter 'instituicoes_id'
      2) Consulta 'instituicoes' pelo ID para obter 'chat_id_telegram'
    """
    # Extrai o nome da câmera do registro de detecção
    nome_camera = detecction['nomecamera']

    #TODO: Da deteccao, identificar o nome_camera -> Com o nome_camera, identificar a instituicao_id na tabela
    #TODO: cameras -> Com a instituicao_id, identificar o chatid na tabela instituicoes
    try:
        # Cria cursor que retorna dicts (facilita acessar colunas pelo nome)
        cursor = conexao.cursor(dictionary=True)

        # 1. Busca o instituicoes_id na tabela cameras
        cursor.execute(
            "SELECT instituicoes_id FROM cameras WHERE nome = %s LIMIT 1",
            (nome_camera,)
        )
        camera_row = cursor.fetchone()
        # Extrai o ID da instituição
        instituicao_id = camera_row['instituicoes_id']
        log.info(f"Instituição ID encontrado: {instituicao_id}")
        # 2. Busca o chat_id_telegram na tabela instituicoes
        cursor.execute(
            "SELECT chat_id_telegram FROM instituicoes WHERE id = %s LIMIT 1",
            (instituicao_id,)
        )
        instituicao_row = cursor.fetchone()
        # Extrai o chat_id_telegram
        chatid = instituicao_row['chat_id_telegram']
        log.info(f"Chat ID encontrado: {chatid}")
        return chatid
    
    except Exception as e:
        # Loga erro em caso de qualquer problema nas consultas
        log.error(f"Erro ao buscar nome da instituição: {e}")