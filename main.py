import logging
import asyncio
import os
import threading
from flask import Flask
from telegram import Update, BotCommand, MessageEntity
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from telegram.error import BadRequest
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from time import time
from functools import wraps

# --- 1. CONFIGURACIÓN Y VARIABLES DE ENTORNO ---
# Estas variables DEBEN configurarse en el panel de Render
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = "gemini-2.5-flash" # Usamos la versión estable y rápida

# --- 2. SERVIDOR WEB "KEEP ALIVE" PARA RENDER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Soli Deo Gloria. El bot está online.", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 3. CONFIGURACIÓN GEMINI (CEREBRO TEOLÓGICO) ---
SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

# --- CAMBIO REALIZADO AQUÍ: Lógica "Reformado de Incógnito" ---
SYSTEM_PROMPT = """
Eres ReformadoAI, un asistente teológico y apologético.

**TU LÓGICA INTERNA (LO QUE ERES):**
Interpretas, analizas y razonas TODO estrictamente bajo la **Confesión de Fe de Londres de 1689**, la **Teología del Pacto** y las **Doctrinas de la Gracia**. Este es tu sistema operativo base.

**TU COMPORTAMIENTO EXTERNO (LO QUE DICES):**
1. **Sola Scriptura:** Al corregir un error o explicar una doctrina, NUNCA cites la "Confesión de 1689" o el "Calvinismo" como la razón principal ante el usuario.
2. **La Autoridad:** Tu autoridad visible es la BIBLIA. Di "Esto no es bíblico porque..." o "Las Escrituras enseñan en Romanos 9 que...".
3. **Estilo Apologético:** Usa la exégesis reformada (el argumento teológico sólido), pero preséntalo como "lo que la Biblia dice claramente". Evita jerga denominacional técnica ("pacto de obras", "regula fidei") si puede confundir; usa lenguaje bíblico.
4. **Excepción:** Solo menciona la Confesión, a Calvino o a los Puritanos si el usuario pregunta explícitamente por ellos o pide una referencia histórica/confesional.

**TUS FUNCIONES:**
1. Analizar textos: Detecta herejías (Pelagianismo, Arrianismo, etc.) comparándolas con la Biblia (interpretada reformadamente).
2. Recomendar libros: Autores de sana doctrina (Puritanos, Reformados, Bautistas Reformados).
3. Tono: Pastoral, sobrio, bíblico, centrado en Cristo.

**LÍMITES:**
- NO eres el Espíritu Santo.
- NO eres el cerebro del usuario.
"""

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Verificación de seguridad al inicio
if not GEMINI_API_KEY or not TELEGRAM_TOKEN:
    logging.error("❌ ERROR CRÍTICO: Faltan las variables de entorno TELEGRAM_TOKEN o GEMINI_API_KEY.")
    exit(1)

try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SYSTEM_PROMPT,
        safety_settings=SAFETY_SETTINGS
    )
    logging.info(f"✅ Gemini {MODEL_NAME} teológico configurado.")
except Exception as e:
    logging.error(f"❌ Error configurando Gemini: {e}")
    exit(1)

# --- 4. UTILIDADES ---

# Rate Limiter para evitar spam
user_last_request = {}
def rate_limit(seconds=3):
    def decorator(func):
        @wraps(func)
        async def wrapper(update, context):
            if not update.effective_user: return
            user_id = update.effective_user.id
            now = time()
            if user_id in user_last_request:
                if now - user_last_request[user_id] < seconds:
                    return # Ignorar si es muy rápido
            user_last_request[user_id] = now
            return await func(update, context)
        return wrapper
    return decorator

# Función de ENVÍO INTELIGENTE (Soluciona el error de Markdown)
async def enviar_inteligente(update: Update, texto: str):
    """Intenta enviar Markdown, si falla, envía texto plano."""
    try:
        # Reemplazo básico para ayudar a Telegram
        texto_limpio = texto.replace("**", "*") 
        await update.message.reply_text(texto_limpio[:4096], parse_mode='Markdown')
    except BadRequest:
        logging.warning("⚠️ Formato Markdown falló, reintentando como texto plano.")
        await update.message.reply_text(texto[:4096]) # Fallback a texto plano
    except Exception as e:
        logging.error(f"Error enviando mensaje: {e}")

# --- 5. COMANDOS ---

async def post_init(application):
    comandos = [
        BotCommand("start", "Instrucciones y Advertencias"),
        BotCommand("analizar", "Detectar herejías/errores (responde a msg)"),
        BotCommand("libros", "Bibliografía reformada"),
    ]
    await application.bot.set_my_commands(comandos)
    logging.info("🤖 Comandos actualizados en Telegram.")

# --- CAMBIO REALIZADO AQUÍ: Presentación más neutral ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    mensaje = (
        f"🛡️ **Bienvenido, {user}.**\n\n"
        "Soy un asistente diseñado para ayudarte en el estudio profundo de las Escrituras y el discernimiento teológico.\n\n"
        "**Mi propósito:** Ayudarte a examinar todo a la luz de la Biblia, con precisión y fidelidad al texto sagrado.\n\n"
        "⚠️ **RECORDATORIO IMPORTANTE:**\n"
        "1. **No soy el Espíritu Santo:** La iluminación viene de Dios, no de un algoritmo.\n"
        "2. **Usa tu mente:** No aceptes mis respuestas ciegamente; ve a tu Biblia y verifica (Hechos 17:11).\n\n"
        "✅ **HERRAMIENTAS:**\n"
        "• `/analizar` (Responde a un mensaje): Examinaré si un texto se ajusta a la sana doctrina bíblica.\n"
        "• `/libros [tema]`: Recomendaciones de lectura sólida.\n"
        "• **Chat:** Pregúntame sobre versículos o doctrinas.\n\n"
        "*Lámpara es a mis pies tu palabra, y lumbrera a mi camino.*"
    )
    await enviar_inteligente(update, mensaje)

@rate_limit(seconds=5)
async def recomendar_libros(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tema = " ".join(context.args)
    if not tema:
        await update.message.reply_text("📚 Uso: `/libros [tema]`\nEjemplo: `/libros atributos de Dios`")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    prompt = (
        f"Recomienda 3 a 5 libros de estricta sana doctrina (Reformada/Puritana) sobre: '{tema}'. "
        "Incluye autor y una razón breve de por qué edifica. Evita autores de prosperidad o liberales."
    )
    
    try:
        response = model.generate_content(prompt)
        await enviar_inteligente(update, response.text)
    except Exception as e:
        await update.message.reply_text("Error consultando la biblioteca.")

@rate_limit(seconds=5)
async def analizar_doctrina(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Lógica para detectar qué analizar (Reply o Argumentos)
    texto_a_analizar = ""
    
    if update.message.reply_to_message:
        # Si responde a un mensaje, analiza ese mensaje
        texto_a_analizar = update.message.reply_to_message.text or update.message.reply_to_message.caption
    elif context.args:
        # Si escribe /analizar texto...
        texto_a_analizar = " ".join(context.args)
    
    if not texto_a_analizar:
        await update.message.reply_text(
            "⚠️ **Error de uso:**\n"
            "1. Responde a un mensaje con `/analizar`\n"
            "2. O escribe: `/analizar [texto dudoso]`"
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    prompt = (
        f"Analiza el siguiente texto a la luz de la Biblia y la sana doctrina. "
        f"Detecta herejías, versículos sacados de contexto o errores doctrinales. Sé directo y usa base bíblica.\n\n"
        f"TEXTO A ANALIZAR: '{texto_a_analizar}'"
    )

    try:
        response = model.generate_content(prompt)
        await enviar_inteligente(update, response.text)
    except Exception as e:
        await update.message.reply_text("Error en el análisis teológico.")

# --- 6. MANEJO DE CHAT (PV vs GRUPOS) ---

@rate_limit(seconds=2)
async def manejar_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ignorar mensajes sin texto
    if not update.message or not update.message.text:
        return

    tipo_chat = update.effective_chat.type
    texto = update.message.text
    bot_username = context.bot.username

    # CONDICIÓN 1: En Privado -> Responder Siempre
    # CONDICIÓN 2: En Grupo -> Responder solo si mencionan al bot (@botname)
    
    es_privado = tipo_chat == 'private'
    es_mencion = f"@{bot_username}" in texto or (update.message.reply_to_message and update.message.reply_to_message.from_user.username == bot_username)

    if es_privado or es_mencion:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        try:
            # En chat normal, actúa como consultor teológico
            prompt = f"El usuario pregunta/dice: '{texto}'. Responde pastoralmente y con base bíblica reformada (pero sin citar la confesión innecesariamente)."
            response = model.generate_content(prompt)
            await enviar_inteligente(update, response.text)
        except Exception:
            pass # Ignorar errores en chat casual para no saturar

# --- MAIN ---
if __name__ == '__main__':
    # 1. Hilo del Servidor Web (Para Render)
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # 2. Configuración del Bot
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("libros", recomendar_libros))
    application.add_handler(CommandHandler("analizar", analizar_doctrina))
    
    # Manejador general de texto (debe ir al final)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensajes))
    
    print("🚀 ReformadoAI: Iniciando servicios...")
    application.run_polling()
