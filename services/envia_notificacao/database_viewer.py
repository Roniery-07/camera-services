# Importa o conector MySQL para Python para acessar o banco de dados
import mysql.connector
# Importa o pandas para manipulação e exibição tabular dos dados
import pandas as pd

# Conexão ao Banco de Dados
# Nome do banco de dados (schema) alvo
DB_NAME = "AoFessays"
# Usuário do banco de dados
DB_USER = "AoFessays"
# Senha do banco de dados
DB_PASS = "UMUStK-y[Ui3NfOF"
# Host do servidor MySQL
DB_HOST = "10.0.5.103"

# Lista de tabelas que serão visualizadas por padrão
TABELAS_FOCO = ["deteccoes_eventos", "eventos"]

def visualizar_tabela(cursor, nome_tabela, n_linhas=5):
    # Imprime separador visual
    print(f"\n{'='*60}")
    # Título para a seção de estrutura da tabela
    print(f"Estrutura da tabela '{nome_tabela}':")
    print(f"{'='*60}")
    # Executa o comando DESCRIBE para ver as colunas e tipos
    cursor.execute(f"DESCRIBE {nome_tabela}")
    # Recupera a descrição das colunas (nome, tipo, nulidade etc.)
    colunas = cursor.fetchall()
    # Percorre e imprime cada definição de coluna
    for coluna in colunas:
        print(coluna)

    # Imprime separador visual
    print(f"\n{'='*60}")
    # Título para a seção de amostra de linhas iniciais
    print(f"Primeiras {n_linhas} linhas da tabela '{nome_tabela}':")
    print(f"{'='*60}")
    # Consulta as primeiras N linhas da tabela
    cursor.execute(f"SELECT * FROM {nome_tabela} LIMIT {n_linhas}")
    # Busca os dados retornados
    dados = cursor.fetchall()
    # Extrai os nomes das colunas a partir da descrição do cursor após o SELECT
    nomes_colunas = [col[0] for col in cursor.description]
    # Se houver dados, monta um DataFrame para exibição legível
    if dados:
        df = pd.DataFrame(dados, columns=nomes_colunas)
        # Imprime o DataFrame sem o índice
        print(df.to_string(index=False))
    else:
        # Mensagem quando a tabela estiver vazia
        print("Nenhuma entrada encontrada.")

    # Imprime separador visual
    print(f"\n{'='*60}")
    # Título para a seção de amostra de linhas finais
    print(f"Últimas {n_linhas} linhas da tabela '{nome_tabela}':")
    print(f"{'='*60}")
    #continuar relatório a partir daqui
    # Define coluna de ID para ordenação conforme tabela
    # Para a tabela 'eventos', utiliza a coluna 'id'
    if nome_tabela.lower() == "eventos":
        coluna_id = "id"
    # Para a tabela 'deteccoes_eventos', escolhe 'eventos_id' (outra opção poderia ser 'deteccoes_id')
    elif nome_tabela.lower() == "deteccoes_eventos":
        coluna_id = "eventos_id"  # pode usar deteccoes_id se preferir
    else:
        # Fallback: usa a primeira coluna do resultado anterior
        coluna_id = nomes_colunas[0]  # fallback

    # Seleciona as últimas N linhas ordenando de forma decrescente pela coluna escolhida
    cursor.execute(f"SELECT * FROM {nome_tabela} ORDER BY {coluna_id} DESC LIMIT {n_linhas}")
    dados_fim = cursor.fetchall()
    # Se houver dados finais, monta DataFrame e inverte a ordem para exibição crescente
    if dados_fim:
        df_fim = pd.DataFrame(dados_fim, columns=nomes_colunas)
        # iloc[::-1] inverte a ordem; to_string imprime sem índice
        print(df_fim.iloc[::-1].to_string(index=False))  # exibe na ordem crescente
    else:
        print("Nenhuma entrada encontrada.")


def visualizar_banco_de_dados():
    # Envolve a conexão e a navegação nas tabelas em um bloco try/except/finally para robustez
    try:
        # Abre a conexão com o MySQL usando os parâmetros definidos acima
        conexao = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        # Verifica se a conexão foi estabelecida
        if conexao.is_connected():
            # Cria um cursor para executar comandos SQL
            cursor = conexao.cursor()
            # Cabeçalho da seção de visualização
            print("\n" + "="*60)
            print("Visualização das tabelas principais:")
            print("="*60)
            # Para cada tabela de interesse, chama a função de visualização
            for nome_tabela in TABELAS_FOCO:
                visualizar_tabela(cursor, nome_tabela, n_linhas=5)
    # Captura erros de conexão ou execução de query
    except mysql.connector.Error as e:
        print(f"Erro ao conectar ou executar a consulta: {e}")
    finally:
        # Garante o fechamento da conexão e do cursor caso estejam abertos
        if 'conexao' in locals() and conexao and conexao.is_connected():
            cursor.close()
            conexao.close()
            print("\nConexão com o MySQL fechada.")


# Ponto de entrada do script: executa a visualização quando chamado diretamente
if __name__ == "__main__":
    visualizar_banco_de_dados()