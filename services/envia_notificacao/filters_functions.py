# Módulo de registro (logging) para gravar logs de execução
import logging
# timedelta será usado para janelas de tempo fixas (ex.: 30 minutos)
from datetime import timedelta
# Pandas para manipulação tabular
import pandas as pd

# Define o intervalo de repetição em minutos
INTERVALO_REPETICAO_MINUTOS = 30

# Nomes dos arquivos de log
log_file = "notifications.log"
results_file = "results.log"

# Inicializa dois loggers separados: um para resultados e outro para operação geral
results = logging.getLogger(results_file)
results.setLevel(logging.INFO) 
log = logging.getLogger(log_file)
log.setLevel(logging.INFO)

# Handlers que escrevem em arquivo
file_handler_results = logging.FileHandler(results_file)
file_handler_log = logging.FileHandler(log_file)

# Formatação padrão para as mensagens de log (data/hora, nível, mensagem)
formatter = logging.Formatter(
                            '[%(asctime)s] - %(levelname)s - %(message)s',
                            datefmt='%d-%b-%Y %H:%M:%S'
                            )

# Aplica o formatter aos handlers
file_handler_results.setFormatter(formatter)
file_handler_log.setFormatter(formatter)

# Associa os handlers aos loggers
results.addHandler(file_handler_results)
log.addHandler(file_handler_log)

def identify_repetition(df: pd.DataFrame, intervalo_minutos = INTERVALO_REPETICAO_MINUTOS) -> pd.DataFrame:
    """
    Marca como repetição todas as detecções que ocorrerem até X minutos após a primeira
    detecção (âncora) de uma mesma câmera. Quando houver um gap > X min, começa uma nova janela.
    Pré-condição:
      - df['datahora'] é datetime
      - df['repeticao'] existe (caso não exista, será criada como False)
    """
    # Se a coluna ainda não existir, cria com valor padrão False
    if 'repeticao' not in df.columns:
        df['repeticao'] = False  # segurança, caso não esteja inicializado

    # Converte o intervalo em um timedelta
    intervalo = timedelta(minutes=intervalo_minutos)

    # Processa separadamente por nome de câmera
    for camera in df['nomecamera'].dropna().unique():
        # Encontra os índices das linhas daquela câmera
        idx_cam = df.index[df['nomecamera'] == camera]
        # Ordena os índices por data/hora em ordem crescente
        ordem_asc = df.loc[idx_cam, 'datahora'].sort_values(ascending=True).index

        # Marca da primeira detecção na janela atual (âncora)
        ancora_time = None  # "primeira detecção" da janela atual

        # Itera sobre as detecções dessa câmera em ordem temporal
        for i in ordem_asc:
            # Timestamp da detecção na linha i
            t = df.at[i, 'datahora']
            # Ignora registros sem data válida
            if pd.isna(t):
                continue  # ignora timestamps inválidos

            # Se não há âncora definida ou o tempo atual excede a janela X desde a âncora
            if (ancora_time is None) or (t > ancora_time + intervalo):
                # Inicia um novo ciclo de janela: esta é a "primeira" detecção (não repetição)
                ancora_time = t
                # df.at[i, 'repeticao'] permanece False por padrão
            else:
                # Ainda dentro da janela de X minutos a partir da âncora -> é repetição
                df.at[i, 'repeticao'] = True

    # Retorna o DataFrame modificado in-place com a marcação de repetição
    return df

# Import redundante (pandas já importado acima), preservado para compatibilidade com histórico do arquivo
import pandas as pd

def agrupar_deteccoes_em_eventos(
    tabela_deteccoes: pd.DataFrame,
    tabela_eventos: pd.DataFrame,
    tabela_deteccoes_eventos: pd.DataFrame
):
    """
    Agrupa detecções em eventos considerando a coluna 'repeticao' e o agrupamento por câmera.
    A cada detecção não repetida, cria um novo evento; detecções repetidas associam-se ao
    último evento aberto daquela câmera.
    """
    # Se não há detecções novas, não há nada para agrupar
    if tabela_deteccoes.empty:
        return tabela_eventos, tabela_deteccoes_eventos

    # Garante que 'datahora' está no tipo datetime (se vier como string/objeto)
    tabela_deteccoes = tabela_deteccoes.copy()
    if pd.api.types.is_object_dtype(tabela_deteccoes['datahora']):
        tabela_deteccoes['datahora'] = pd.to_datetime(tabela_deteccoes['datahora'])

    # Identifica detecções já mapeadas em eventos para evitar duplicidade
    ids_mapeados = set(tabela_deteccoes_eventos['deteccoes_id'].unique())
    deteccoes_novas = tabela_deteccoes[~tabela_deteccoes['id'].isin(ids_mapeados)].copy()
    
    # Se nenhuma detecção nova sobrou, retorna as tabelas intactas
    if deteccoes_novas.empty:
        return tabela_eventos, tabela_deteccoes_eventos

    # Ordena as novas detecções por câmera e tempo (para manter sequência)
    deteccoes_novas.sort_values(['nomecamera', 'datahora'], inplace=True)

    # Caso existam links e eventos anteriores, calcula para cada câmera qual foi o último evento
    if not tabela_deteccoes_eventos.empty and not tabela_eventos.empty:
        # Junta links com detecções para obter 'nomecamera'
        df_merged = tabela_deteccoes_eventos.merge(tabela_deteccoes[['id', 'nomecamera']], left_on='deteccoes_id', right_on='id', how='left')
        # Junta com eventos para obter 'datahora_abertura'
        df_merged = df_merged.merge(tabela_eventos, left_on='eventos_id', right_on='id', how='left')
        
        # Para cada câmera, pega a linha do evento mais recente (maior datahora_abertura)
        ultimo_evento_info = df_merged.groupby('nomecamera').apply(lambda x: x.loc[x['datahora_abertura'].idxmax()])
        # Constrói um dict com {camera: {'id': <id_evento>, 'datahora_abertura': <data>}}
        ultimo_evento_camera = ultimo_evento_info[['eventos_id', 'datahora_abertura']].rename(columns={'eventos_id': 'id'}).to_dict('index')
    else:
        # Se não houver referência anterior, começa com mapa vazio
        ultimo_evento_camera = {}
    
    # Mapeia nome da câmera -> ID do último evento
    ultimo_evento_id_por_camera = {cam: info['id'] for cam, info in ultimo_evento_camera.items()}

    # Listas acumuladoras para novos eventos e novos links detecção<->evento
    novo_eventos_lista = []
    novo_links_lista = []
    
    # Calcula o próximo ID de evento a partir do maior ID existente (ou 1 se vazio)
    prox_evento_id = tabela_eventos['id'].max() + 1 if not tabela_eventos.empty else 1
    
    # Percorre as detecções novas e decide se abre novo evento ou associa ao último
    for _, row in deteccoes_novas.iterrows():
        camera = row['nomecamera']
        det_id = row['id']
        datahora = row['datahora']
        repeticao = row.get('repeticao', False)

        if not repeticao:
            # Não repetição: cria novo evento
            evento_id = prox_evento_id
            prox_evento_id += 1
            novo_eventos_lista.append({'id': evento_id, 'datahora_abertura': datahora})
            
            # Atualiza "último evento" para esta câmera
            ultimo_evento_id_por_camera[camera] = evento_id
        else:
            # Repetição: associa ao último evento conhecido da câmera
            if camera in ultimo_evento_id_por_camera:
                evento_id = ultimo_evento_id_por_camera[camera]
            else:
                # Caso não exista evento anterior para esta câmera, cria um novo
                evento_id = prox_evento_id
                prox_evento_id += 1
                novo_eventos_lista.append({'id': evento_id, 'datahora_abertura': datahora})
        
        # Registra o link entre a detecção atual e o evento definido acima
        novo_links_lista.append({'eventos_id': evento_id, 'deteccoes_id': det_id})

    # Concatena os novos eventos com os existentes (se houver)
    novos_eventos_df = pd.DataFrame(novo_eventos_lista)
    if not novos_eventos_df.empty:
        tabela_eventos_atualizada = pd.concat([tabela_eventos, novos_eventos_df], ignore_index=True)
    else:
        tabela_eventos_atualizada = tabela_eventos

    # Concatena os novos links com os existentes (se houver)
    novos_links_df = pd.DataFrame(novo_links_lista)
    if not novos_links_df.empty:
        tabela_deteccoes_eventos_atualizada = pd.concat([tabela_deteccoes_eventos, novos_links_df], ignore_index=True)
    else:
        tabela_deteccoes_eventos_atualizada = tabela_deteccoes_eventos
    
    # Retorna as duas tabelas atualizadas
    return tabela_eventos_atualizada, tabela_deteccoes_eventos_atualizada

def get_eventos_para_notificar(tabela_eventos_pd: pd.DataFrame):
    """
    Seleciona os eventos que ainda não foram notificados, filtrando por data mínima.
    Considera eventos com 'datahora_notificado' nulo e data >= corte.
    """
    # Nome da coluna de data de abertura dos eventos
    coluna_de_data_do_evento = 'datahora_abertura'  # Ajuste conforme o nome correto da coluna de data do evento

    # Converte para datetime para fazer comparações temporais de forma segura
    tabela_eventos_pd[coluna_de_data_do_evento] = pd.to_datetime(tabela_eventos_pd[coluna_de_data_do_evento])
    
    # Define a data de corte (somente eventos a partir desta data serão considerados)
    data_corte = pd.to_datetime('2025-09-18')
    
    # Filtra por eventos cuja coluna 'datahora_notificado' é NaN e a data é posterior/igual ao corte
    eventos_para_notificar = tabela_eventos_pd[
        (tabela_eventos_pd['datahora_notificado'].isna()) &
        (tabela_eventos_pd[coluna_de_data_do_evento] >= data_corte)
    ]
    
    # Retorna apenas os eventos pendentes de notificação
    return eventos_para_notificar

def get_deteccao_id_por_evento_id(tabela_deteccoes_eventos: pd.DataFrame, eventos_id: int):
    """
    Retorna o primeiro ID de detecção associado a um evento na estrutura normalizada,
    onde cada linha corresponde a uma detecção vinculada a um evento.
    """
    try:
        # Seleciona a coluna de 'deteccoes_id' para o evento informado
        deteccoes = tabela_deteccoes_eventos.loc[
            tabela_deteccoes_eventos['eventos_id'] == eventos_id,
            'deteccoes_id'
        ]

        # Nenhum resultado encontrado
        if deteccoes.empty:
            return None

        # Retorna o menor ID (em geral, o primeiro da linha temporal do evento)
        return int(deteccoes.min())

    except Exception:
        # Em caso de qualquer erro, retorna None silenciosamente
        return None


def save_to_database(tabela_eventos_pd, tabela_deteccoes_eventos_pd, conexao):
    """
    Atualiza o banco com novos eventos e novos vínculos detecção<->evento.
    Também atualiza 'datahora_notificado' de eventos já existentes quando aplicável.
    """
    try:
        # Lê o estado atual do banco, permitindo comparar e inserir apenas o que for novo
        tabela_eventos_original_pd = pd.read_sql("SELECT * FROM eventos", conexao)
        tabela_deteccoes_eventos_original_pd = pd.read_sql("SELECT * FROM deteccoes_eventos", conexao)

        # 1) Novos eventos são aqueles cujo 'id' ainda não existe na tabela real
        novos_eventos = tabela_eventos_pd[~tabela_eventos_pd['id'].isin(tabela_eventos_original_pd['id'])]
        if not novos_eventos.empty:
            with conexao.cursor() as cursor:
                # Inserção linha a linha para cada novo evento
                for _, row in novos_eventos.iterrows():
                    query = """
                        INSERT INTO eventos 
                        (id, datahora_abertura, datahora_visto, datahora_entregue_push, datahora_notificado)
                        VALUES (%s, %s, %s, %s, %s)
                    """
                    cursor.execute(query, (
                        row['id'], 
                        row['datahora_abertura'], 
                        row['datahora_visto'], 
                        row['datahora_entregue_push'], 
                        row['datahora_notificado']
                    ))
            # Efetiva as inserções
            conexao.commit()
            results.info(f"{len(novos_eventos)} novos eventos adicionados.")

        # 2) Novas ligações são pares (eventos_id, deteccoes_id) que ainda não existem
        novas_ligacoes = tabela_deteccoes_eventos_pd[~tabela_deteccoes_eventos_pd[['eventos_id', 'deteccoes_id']]
            .apply(tuple, axis=1)
            .isin(tabela_deteccoes_eventos_original_pd[['eventos_id', 'deteccoes_id']].apply(tuple, axis=1))
        ]

        if not novas_ligacoes.empty:
            with conexao.cursor() as cursor:
                # Insere cada nova ligação
                for _, row in novas_ligacoes.iterrows():
                    query = """
                        INSERT INTO deteccoes_eventos (eventos_id, deteccoes_id) 
                        VALUES (%s, %s)
                    """
                    cursor.execute(query, (int(row['eventos_id']), int(row['deteccoes_id'])))
            # Confirma as inserções
            conexao.commit()
            results.info(f"{len(novas_ligacoes)} novas ligações adicionadas.")

        # 3) Atualiza eventos existentes que agora possuem 'datahora_notificado' preenchida
        eventos_alterados = tabela_eventos_pd.loc[
            tabela_eventos_pd['datahora_notificado'].notna() &
            tabela_eventos_pd['id'].isin(tabela_eventos_original_pd['id'])
        ]
        if not eventos_alterados.empty:
            with conexao.cursor() as cursor:
                for _, row in eventos_alterados.iterrows():
                    query = "UPDATE eventos SET datahora_notificado = %s WHERE id = %s"
                    cursor.execute(query, (row['datahora_notificado'], row['id']))
            # Salva as alterações
            conexao.commit()
            results.info(f"{len(eventos_alterados)} eventos atualizados com a data de notificação.")

    except Exception as e:
        # Loga o erro e retorna False para indicar falha
        results.error(f"Erro ao salvar as alterações no banco de dados: {e}")
        return False

    # Sucesso
    return True