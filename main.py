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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID") # <--- ¡NUEVA VARIABLE! Tu ID de Telegram

# Definimos los dos modelos
MODEL_NAME_FLASH = "gemini-2.5-flash"   # Para el pueblo (Rápido, 1500 req/día)
MODEL_NAME_PRO = "gemini-3-pro-preview" # Para el pastor (Inteligente, 50 req/día)

# --- 2. SERVIDOR WEB "KEEP ALIVE" PARA RENDER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Soli Deo Gloria. El bot está online.", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 3. CONFIGURACIÓN GEMINI (DOBLE MOTOR) ---
SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

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

# Verificación de seguridad
if not GEMINI_API_KEY or not TELEGRAM_TOKEN or not OWNER_ID:
    logging.error("❌ ERROR CRÍTICO: Faltan variables (TELEGRAM_TOKEN, GEMINI_API_KEY u OWNER_ID).")
    exit(1)

try:
    genai.configure(api_key=GEMINI_API_KEY)
    
    # MOTOR 1: FLASH (Público)
    model_flash = genai.GenerativeModel(
        model_name=MODEL_NAME_FLASH,
        system_instruction=SYSTEM_PROMPT,
        safety_settings=SAFETY_SETTINGS
    )

    # MOTOR 2: PRO (Privado - Solo para ti)
    model_pro = genai.GenerativeModel(
        model_name=MODEL_NAME_PRO,
        system_instruction=SYSTEM_PROMPT,
        safety_settings=SAFETY_SETTINGS
    )

    logging.info(f"✅ Motores configurados: {MODEL_NAME_FLASH} y {MODEL_NAME_PRO}.")
except Exception as e:
    logging.error(f"❌ Error configurando Gemini: {e}")
    exit(1)

# --- 4. UTILIDADES ---

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
                    return 
            user_last_request[user_id] = now
            return await func(update, context)
        return wrapper
    return decorator

async def enviar_inteligente(update: Update, texto: str):
    """Intenta enviar Markdown, si falla, envía texto plano."""
    try:
        texto_limpio = texto.replace("**", "*") 
        await update.message.reply_text(texto_limpio[:4096], parse_mode='Markdown')
    except BadRequest:
        logging.warning("⚠️ Formato Markdown falló, reintentando como texto plano.")
        await update.message.reply_text(texto[:4096])
    except Exception as e:
        logging.error(f"Error enviando mensaje: {e}")

# --- 5. COMANDOS ---

async def post_init(application):
    comandos = [
        BotCommand("start", "Instrucciones"),
        BotCommand("analizar", "Detectar errores (Flash)"),
        BotCommand("libros", "Bibliografía"),
        BotCommand("pro", "Consulta Avanzada (Solo Admin)"), # Nuevo comando en menú
    ]
    await application.bot.set_my_commands(comandos)
    logging.info("🤖 Comandos actualizados en Telegram.")

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
        response = model_flash.generate_content(prompt)
        await enviar_inteligente(update, response.text)
    except Exception as e:
        await update.message.reply_text("Error consultando la biblioteca.")

@rate_limit(seconds=5)
async def analizar_doctrina(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_a_analizar = ""
    if update.message.reply_to_message:
        texto_a_analizar = update.message.reply_to_message.text or update.message.reply_to_message.caption
    elif context.args:
        texto_a_analizar = " ".join(context.args)
    
    if not texto_a_analizar:
        await update.message.reply_text("⚠️ Responde a un mensaje con `/analizar` o escribe: `/analizar [texto]`")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    prompt = (
        f"Analiza el siguiente texto a la luz de la Biblia y la sana doctrina. "
        f"Detecta herejías, versículos sacados de contexto o errores doctrinales. Sé directo y usa base bíblica.\n\n"
        f"TEXTO A ANALIZAR: '{texto_a_analizar}'"
    )
    try:
        response = model_flash.generate_content(prompt)
        await enviar_inteligente(update, response.text)
    except Exception as e:
        await update.message.reply_text("Error en el análisis teológico.")

# --- COMANDO VIP: CONSULTA PRO (GEMINI 3) ---
async def consulta_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. Verificar si eres TÚ (el dueño)
    user_id = str(update.effective_user.id)
    if user_id != str(OWNER_ID):
        await update.message.reply_text("⛔ **Acceso Denegado.** Este comando usa recursos avanzados y es solo para el administrador.")
        return

    consulta = " ".join(context.args)
    if not consulta:
        await update.message.reply_text("🧠 **Modo Pro (Gemini 3 Preview)**\nUso: `/pro [pregunta compleja]`\n\n*Nota: 50 consultas diarias.*")
        return

    await update.message.reply_text("⏳ **Analizando profundamente (Modelo Pro)...**")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # AQUÍ USAMOS EL MODELO PRO
        response = model_pro.generate_content(consulta)
        await enviar_inteligente(update, response.text)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error en Gemini Pro: {e}")

# --- 6. MANEJO DE CHAT (PV vs GRUPOS) ---

@rate_limit(seconds=2)
async def manejar_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return

    tipo_chat = update.effective_chat.type
    texto = update.message.text
    bot_username = context.bot.username
    
    es_privado = tipo_chat == 'private'
    es_mencion = f"@{bot_username}" in texto or (update.message.reply_to_message and update.message.reply_to_message.from_user.username == bot_username)

    if es_privado or es_mencion:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        try:
            # USAMOS FLASH PARA EL CHAT DIARIO (Ahorrar cuota Pro)
            prompt = f"El usuario pregunta/dice: '{texto}'. Responde pastoralmente y con base bíblica reformada (pero sin citar la confesión innecesariamente)."
            response = model_flash.generate_content(prompt)
            await enviar_inteligente(update, response.text)
        except Exception:
            pass

# --- MAIN ---
if __name__ == '__main__':
    # 1. Servidor Web
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # 2. Bot
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("libros", recomendar_libros))
    application.add_handler(CommandHandler("analizar", analizar_doctrina))
    application.add_handler(CommandHandler("pro", consulta_pro)) # ¡Nuevo Handler Registrado!
    
    # Mensajes generales
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensajes))
    
    print("🚀 ReformadoAI: Iniciando servicios con Doble Motor...")
    application.run_polling()
 
