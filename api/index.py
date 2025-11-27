import logging
import asyncio
import os
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- INICIALIZACIÓN FLASK ---
app = Flask(__name__)

# --- CONFIGURACIÓN DE VARIABLES (Vercel las inyectará) ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL_NAME = "gemini-2.5-flash"

# --- CONFIGURACIÓN GEMINI ---
SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

SYSTEM_PROMPT = """
Eres ReformadoAI, asistente teológico apologético (Confesión de Fe de Londres de 1689).
1. Ortodoxia: Sola Scriptura, Doctrinas de la Gracia.
2. Análisis: Detecta errores y sé pastoral.
NO sustituyas al Espíritu Santo.
"""

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(model_name=MODEL_NAME, system_instruction=SYSTEM_PROMPT, safety_settings=SAFETY_SETTINGS)

# --- CONFIGURACIÓN DEL BOT (Global) ---
# Inicializamos la app de Telegram fuera de la función para intentar reutilizarla si Vercel mantiene la instancia caliente.
ptb_application = None

async def initialize_bot():
    global ptb_application
    if not ptb_application:
        ptb_application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        # --- TUS COMANDOS ---
        ptb_application.add_handler(CommandHandler('start', start))
        ptb_application.add_handler(CommandHandler('analizar', analizar))
        ptb_application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), mensaje_general))
        
        await ptb_application.initialize()
    return ptb_application

# --- HANDLERS (Tu lógica) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    await update.message.reply_text(f"Gracia y paz, {user}. Soy ReformadoAI. Listo para servir.")

async def analizar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)
    if update.message.reply_to_message:
        texto = update.message.reply_to_message.text
    
    if not texto:
        await update.message.reply_text("Por favor, dame un texto para analizar.")
        return

    chat_id = update.effective_chat.id
    # Enviamos acción de escribiendo...
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        # Usamos to_thread para no bloquear el loop principal con la llamada a Google
        response = await asyncio.to_thread(model.generate_content, f"Analiza teologicamente (1689 LBCF): '{texto}'")
        await update.message.reply_text(response.text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("Hubo un error al consultar a Gemini.")

async def mensaje_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Solo responde en privado o si lo mencionan (lógica básica)
    if update.effective_chat.type == 'private':
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        try:
            response = await asyncio.to_thread(model.generate_content, update.message.text)
            await update.message.reply_text(response.text, parse_mode='Markdown')
        except Exception as e:
            logging.error(f"Error: {e}")

# --- RUTAS DE FLASK (El puente con Vercel) ---

@app.route('/', methods=['GET'])
def index():
    return "ReformadoAI operativo. Soli Deo Gloria."

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Esta función recibe el JSON de Telegram y corre el proceso asíncrono.
    """
    if request.method == "POST":
        update_json = request.get_json(force=True)
        
        async def process_update():
            app_bot = await initialize_bot()
            update = Update.de_json(update_json, app_bot.bot)
            await app_bot.process_update(update)

        # Ejecutamos el loop asíncrono
        try:
            asyncio.run(process_update())
        except RuntimeError:
            # Fallback por si ya hay un loop corriendo (raro en Vercel puro, pero posible)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(process_update())
            
        return "OK"
    return "Error"
