import logging
import asyncio
import os
import threading
from flask import Flask
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from time import time
from functools import wraps

# --- 1. CONFIGURACIÓN SEGURA (VARIABLES DE ENTORNO) ---
# En lugar de poner las claves aquí, las pediremos al sistema.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = "gemini-2.0-flash-exp"

# --- 2. SERVIDOR WEB "KEEP ALIVE" PARA RENDER ---
# Esto es necesario para que Render no apague el bot.
app = Flask(__name__)

@app.route('/')
def health_check():
    return "El bot está vivo y sirviendo al Señor.", 200

def run_flask():
    # Render asigna un puerto dinámico en la variable de entorno PORT
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- CONFIGURACIÓN GEMINI ---
SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

SYSTEM_PROMPT = """
Eres ReformadoAI, asistente teológico apologético (Confesión de Fe de Londres de 1689).
DIRECTRICES:
1. Ortodoxia: Sola Scriptura, Doctrinas de la Gracia.
2. Análisis: Detecta herejías, errores y textos fuera de contexto.
3. Estilo: Pastoral, serio, sin rodeos.
REGLAS DE ORO:
- NO sustituyas al Espíritu Santo.
- NO pienses por el usuario; edifícalo.
"""

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Validación de claves antes de iniciar
if not GEMINI_API_KEY or not TELEGRAM_TOKEN:
    logging.error("❌ FALTAN LAS VARIABLES DE ENTORNO. Configúralas en Render.")
    exit(1)

try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SYSTEM_PROMPT,
        safety_settings=SAFETY_SETTINGS
    )
    logging.info(f"✅ Gemini {MODEL_NAME} configurado correctamente")
except Exception as e:
    logging.error(f"❌ Error Gemini: {e}")
    exit(1)

# --- RATE LIMITING ---
user_last_request = {}

def rate_limit(seconds=3):
    def decorator(func):
        @wraps(func)
        async def wrapper(update, context):
            user_id = update.effective_user.id
            now = time()
            if user_id in user_last_request:
                if now - user_last_request[user_id] < seconds:
                    return
            user_last_request[user_id] = now
            return await func(update, context)
        return wrapper
    return decorator

# --- ENVÍO SEGURO ---
async def enviar_respuesta_segura(update: Update, texto: str, reply_id: int = None):
    MAX_LENGTH = 4000
    chat_id = update.effective_chat.id
    # Obtenemos el objeto bot del contexto o update
    bot = context.bot if 'context' in locals() else update.get_bot()

    async def intentar_enviar(bloque):
        try:
            await bot.send_message(chat_id=chat_id, text=bloque, parse_mode='Markdown', reply_to_message_id=reply_id)
        except Exception:
            try:
                await bot.send_message(chat_id=chat_id, text=bloque, reply_to_message_id=reply_id)
            except Exception as e2:
                logging.error(f"Error crítico enviando mensaje: {e2}")

    if len(texto) <= MAX_LENGTH:
        await intentar_enviar(texto)
    else:
        partes = [texto[i:i+MAX_LENGTH] for i in range(0, len(texto), MAX_LENGTH)]
        for parte in partes:
            await intentar_enviar(parte)
            await asyncio.sleep(0.5)

# --- COMANDOS ---
async def post_init(application):
    comandos = [
        BotCommand("start", "Advertencias y Uso"),
        BotCommand("libros", "Bibliografía reformada"),
        BotCommand("analizar", "Detectar errores teológicos"),
    ]
    await application.bot.set_my_commands(comandos)
    bot_info = await application.bot.get_me()
    logging.info(f"🤖 Bot iniciado como @{bot_info.username}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    mensaje = (
        f"🛡️ **ReformadoAI en línea.**\n"
        f"Gracia y paz, {user}.\n\n"
        "⚠️ **ADVERTENCIA FUNDAMENTAL:**\n"
        "1. **No soy el Espíritu Santo.**\n"
        "2. **No sustituyo tu cerebro.**\n\n"
        "✅ **USOS LÍCITOS:**\n"
        "• `/analizar` (responde a un mensaje)\n"
        "• `/libros [tema]`\n"
    )
    # Usamos el método simple para start
    await update.message.reply_text(mensaje, parse_mode='Markdown')

@rate_limit(seconds=5)
async def recomendar_libros(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: `/libros <tema>`")
        return
    tema = " ".join(context.args)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        prompt = f"Recomienda 5 libros reformados sobre: {tema}. Breve descripción."
        response = model.generate_content(prompt)
        # Nota: Aquí deberías llamar a enviar_respuesta_segura, pero por simplicidad:
        await update.message.reply_text(response.text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("Error temporal.")

@rate_limit(seconds=5)
async def analizar_doctrina(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = ""
    if update.message.reply_to_message:
        texto = update.message.reply_to_message.text or update.message.reply_to_message.caption
    elif context.args:
        texto = " ".join(context.args)
    
    if not texto:
        await update.message.reply_text("Responde a un mensaje con `/analizar`.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        prompt = f"Analiza teológicamente según la Confesión de Fe de 1689: '{texto}'"
        response = model.generate_content(prompt)
        await update.message.reply_text(response.text[:4000], parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text("Error en el análisis.")

# --- MAIN EJECUCIÓN ---
if __name__ == '__main__':
    # 1. Iniciar el servidor Flask en un hilo separado
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # 2. Iniciar el Bot
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("libros", recomendar_libros))
    application.add_handler(CommandHandler("analizar", analizar_doctrina))
    
    # Manejador de menciones en grupos
    # Nota: Para que funcione en grupos, el bot debe tener permisos y Privacy Mode desactivado en BotFather si quieres que lea todo,
    # pero para menciones específicas, usa filtros.
    
    print("🚀 Bot ReformadoAI iniciando...")
    application.run_polling()
