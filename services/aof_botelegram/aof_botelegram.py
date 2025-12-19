# bot_app.py
import logging
import os
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import random
import json
import mysql.connector
from datetime import datetime
from sdnotify import SystemdNotifier

# Carrega arquivo de configurações
with open("/usr/local/src/AoFNGD/src/config.json", "r") as f:
    config_sys = json.load(f)

# Comunicação com whatchdog
notifier = SystemdNotifier()

#
# ==== FUNÇÕES AUXILIARES
#

# Marca a notificação como visto
async def marca_notificacao_visto(notificacao_id, host=config_sys["database"]["host"], user=config_sys["database"]["user"], password=config_sys["database"]["password"], database=config_sys["database"]["database"]):
    try:
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database
        )
        cursor = conn.cursor()

        query = """
            UPDATE notificacoes SET datahora_visto = NOW() WHERE id = %s
        """

        values = (notificacao_id,)

        cursor.execute(query, values)
        conn.commit()

        if cursor.rowcount == 0:
            # Nenhuma linha atualizada
            return False
        return True
    
    except mysql.connector.Error as e:
        logging.exception(f"Falha ao inserir notificação no MySQL: {e}")
        return False
        
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

# Configura Logs
def setup_logging(log_dir="/var/log", log_file="aof_botelegram.log"):
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, log_file)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            # Exibir no console
            logging.StreamHandler()
        ]
    )
    
    # Silencia logs do httpx
    logging.getLogger("httpx").setLevel(logging.ERROR)
    # Silencia logs do apscheduler
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

#
# ==== FUNÇÕES DO BOT
#

# Comando /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("Comando /start de %s", update.effective_user.username)
    await update.message.reply_text("Olá! Eu sou o Bot do Telegram do Projeto Apaga o Fogo, um projeto destinado a identificar incêndios florestais em estágio precoce a partir de imagens captadas por câmeras e processadas por algoritmos de inteligência artificial. Para saber mais a respeito do projeto acesse o site https://apagaofogo.eco.br.")

# Processamento dos Callbacks
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logging.info("Callback recebido: %s", query.data)

    # Dados do callback
    dados = query.data
    user = query.from_user

    # Callback: Visto
    if dados.startswith("ntfvisto|"):
        _, notificacao_id = dados.split("|")
        notificacao_id = int(notificacao_id)
        if await marca_notificacao_visto(notificacao_id):
            logging.info(f"O usuário {user.full_name} deu visto na notificação id: {notificacao_id}")
            # Remove o botão e altera o caption
            agora = datetime.now().strftime("%d/%m/%Y às %H:%M")
            novo_texto = f"{query.message.caption or query.message.text}\n\nVisto por {user.full_name} em {agora}"
            await query.edit_message_caption(
                caption=novo_texto,
                reply_markup=None
            )
        else:
            logging.error(f"O usuário {user.full_name} deu visto na notificação id: {notificacao_id} mas houve erro ao executar a atualização!")
    else:
        await query.edit_message_text("Callback não reconhecido.")

# Responde com uma mensagem aleatória a comandos não reconhecidos
async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensagens = [
        "Sei... Enfim, você sabia que estou observando constantemente áreas de preservação ambiental para protegê-las de incêndios? Para saber mais a respeito do projeto acesse o site https://apagaofogo.eco.br.",
        "Ok!... Mudando de assunto, você sabia que eu consigo detectar fogo e fumaça por meio de imagens de câmeras? Para saber mais a respeito do projeto acesse o site https://apagaofogo.eco.br.",
        "Não entendi... Mas, você sabia que estou em constante sintonia com a comunidade, recebendo feedbacks de usuários do site Apaga o Fogo confirmando a presença de focos de incêndio em áreas de preservação ambiental? Para saber mais a respeito do projeto acesse o site https://apagaofogo.eco.br."
    ]
    resposta = random.choice(mensagens)
    logging.warning("Texto desconhecido: %s", update.message.text)
    await update.message.reply_text(resposta)

# Caso ocorra algum problema com os handlers anteriores, é repassado essa mensagem!
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("Erro: %s", context.error, exc_info=True)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Desculpe, estou muito ocupado agora apagando um incêndio!!! Tente novamente mais tarde.")

# Sinaliza vida para o systemd
async def watchdog(context):
    notifier.notify("WATCHDOG=1")

def main():
    # Configurando logs
    setup_logging()
    # Inicinado Bot
    app = ApplicationBuilder().token( config_sys["telegram"]["bot_token"] ).build()
    # Handlers para os serviços do bot
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, unknown_text))
    app.add_error_handler(error_handler)
    # Sinalizando vida para o systemd a cada 10 segundos
    app.job_queue.run_repeating(watchdog, interval=10)
    # Inicialização
    logging.info("Bot iniciado e aguardando comandos.")
    app.run_polling()

if __name__ == "__main__":
    main()
