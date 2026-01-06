import asyncio
import html
import logging
import os
import threading
from dataclasses import dataclass
from time import time
from typing import Optional

from flask import Flask
from telegram import BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Legacy SDK (EOL Nov 30, 2025). Consider migrating to google-genai.
import google.generativeai as genai
from google.generativeai.types import HarmBlockThreshold, HarmCategory

try:
    # Optional (useful for local dev). In Render, env vars are set in dashboard.
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:
    pass


# -----------------------------
# 1) CONFIG
# -----------------------------
@dataclass(frozen=True)
class Config:
    telegram_token: str
    gemini_api_key: str
    owner_id: str
    model_flash: str
    model_pro: str
    max_msg_chars: int = 3900  # buffer under Telegram's 4096 limit


def must_get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


CONFIG = Config(
    telegram_token=must_get_env("TELEGRAM_TOKEN"),
    gemini_api_key=must_get_env("GEMINI_API_KEY"),
    owner_id=must_get_env("OWNER_ID"),
    # Gemini 3 Flash (preview)
    model_flash="gemini-3-flash-preview",
    # Gemini 3 Pro (preview)
    model_pro="gemini-3-pro-preview",
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("reformado_bot")


# -----------------------------
# 2) RENDER KEEP-ALIVE (Flask)
# -----------------------------
app = Flask(__name__)


@app.route("/")
def health_check():
    return "Soli Deo Gloria. El bot está online.", 200


def run_flask():
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, threaded=True)


# -----------------------------
# 3) GEMINI CONFIG
# -----------------------------
SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

SYSTEM_PROMPT = """
Eres ReformadoAI, un asistente teológico y apologético.

PRIORIDADES (orden):
1) Fidelidad bíblica (Sola Scriptura): tu autoridad citada es la Escritura.
2) Claridad y precisión: define términos, evita ambigüedad.
3) Caridad y firmeza: corrige con mansedumbre, sin suavizar el error.
4) Utilidad: da pasos concretos (application), no solo teoría.

MARCO DOCTRINAL (interno):
- Teología reformada bautista (Confesión de Londres 1689) como marco de coherencia.
- Teología del Pacto y Doctrinas de la Gracia como lente.
- IMPORTANTE: No cites la Confesión como argumento principal. Solo si el usuario lo pide explícitamente.

REGLAS DE RESPUESTA:
- Siempre que hagas una afirmación doctrinal, apóyala con 1–3 textos bíblicos relevantes.
- Si el usuario cita un versículo, examina contexto inmediato y contexto canónico.
- Si faltan datos para responder, pregunta 1–2 preguntas de clarificación.
- Distingue entre: (a) doctrina central, (b) doctrina secundaria, (c) opinión prudencial.
- Evita “fluff”. Sé directo, sobrio, pastoral.

TAREAS:
- Analizar textos: detectar errores doctrinales, eisegesis, o versículos fuera de contexto.
- Recomendar libros: autores reformados/puritanos, con motivo breve.
- Consejería: siempre subordina a Escritura y prudencia pastoral; recuerda límites del asistente.

LÍMITES:
- No eres el Espíritu Santo.
- No sustituyes al pastor local ni a la iglesia.
- No inventes citas bíblicas: si no estás seguro, dilo.
""".strip()

genai.configure(api_key=CONFIG.gemini_api_key)

model_flash = genai.GenerativeModel(
    model_name=CONFIG.model_flash,
    system_instruction=SYSTEM_PROMPT,
    safety_settings=SAFETY_SETTINGS,
)
model_pro = genai.GenerativeModel(
    model_name=CONFIG.model_pro,
    system_instruction=SYSTEM_PROMPT,
    safety_settings=SAFETY_SETTINGS,
)

logger.info("Motores configurados: %s y %s", CONFIG.model_flash, CONFIG.model_pro)

# Concurrency guard to avoid bursting your Render instance + API
GEMINI_SEMAPHORE = asyncio.Semaphore(4)


# -----------------------------
# 4) UTILITIES
# -----------------------------
_user_last_request: dict[int, float] = {}
_rate_limit_calls = 0


def rate_limit(seconds: int = 3):
    """
    Per-user throttling with periodic cleanup to avoid unbounded growth.
    """
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            global _rate_limit_calls
            if not update.effective_user:
                return

            user_id = update.effective_user.id
            now = time()

            last = _user_last_request.get(user_id, 0.0)
            if now - last < seconds:
                return

            _user_last_request[user_id] = now
            _rate_limit_calls += 1

            # Periodic cleanup
            if _rate_limit_calls % 250 == 0:
                cutoff = now - max(60, seconds * 30)
                stale = [uid for uid, ts in _user_last_request.items() if ts < cutoff]
                for uid in stale:
                    _user_last_request.pop(uid, None)

            return await func(update, context)
        return wrapper
    return decorator


def _chunk_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return ["(respuesta vacía)"]

    chunks = []
    start = 0
    length = len(text)

    while start < length:
        end = min(start + max_chars, length)

        # Try not to cut mid-sentence or mid-paragraph
        if end < length:
            pivot = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if pivot > start + 200:
                end = pivot + (0 if text[pivot] == "\n" else 1)

        chunks.append(text[start:end].strip())
        start = end

    return chunks


async def send_safe_html(update: Update, text: str) -> None:
    """
    Sends text safely using HTML parse mode (escaped),
    in multiple chunks if needed.
    """
    if not update.message:
        return

    chunks = _chunk_text(text, CONFIG.max_msg_chars)
    for piece in chunks:
        safe = html.escape(piece)
        try:
            await update.message.reply_text(safe, parse_mode=ParseMode.HTML)
        except BadRequest:
            await update.message.reply_text(piece)


async def gemini_generate(model, prompt: str) -> str:
    """
    Runs blocking Gemini call in a thread so PTB async loop stays responsive.
    """
    async with GEMINI_SEMAPHORE:
        resp = await asyncio.to_thread(model.generate_content, prompt)

    return getattr(resp, "text", "") or ""


def is_mentioned_in_group(update: Update, bot_username: Optional[str]) -> bool:
    """
    In groups: respond ONLY if message contains @bot_username.
    Replies to the bot do NOT trigger responses.
    """
    if not update.message or not update.message.text or not bot_username:
        return False
    return f"@{bot_username}" in update.message.text


# -----------------------------
# 5) COMMANDS
# -----------------------------
async def post_init(application):
    commands = [
        BotCommand("start", "Instrucciones"),
        BotCommand("analizar", "Analizar texto (Gemini 3 Flash)"),
        BotCommand("libros", "Bibliografía"),
        BotCommand("pro", "Consulta avanzada (solo admin)"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Comandos actualizados en Telegram.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name if update.effective_user else "hermano"
    msg = (
        f"<b>🛡️ Bienvenido, {html.escape(user)}.</b>\n\n"
        "Soy un asistente para ayudarte a examinar doctrina y Escritura con discernimiento.\n\n"
        "<b>⚠️ Recordatorio:</b>\n"
        "1) No soy el Espíritu Santo.\n"
        "2) Verifica todo en tu Biblia (Hechos 17:11).\n\n"
        "<b>✅ Comandos:</b>\n"
        "• <code>/analizar</code> (responde a un mensaje o pega texto)\n"
        "• <code>/libros tema</code>\n"
        "• Chat en privado o mención con @ en grupo.\n\n"
        "<i>Lámpara es a mis pies tu palabra, y lumbrera a mi camino.</i>"
    )
    if update.message:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


@rate_limit(seconds=5)
async def recomendar_libros(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tema = " ".join(context.args).strip()
    if not tema:
        await send_safe_html(update, "📚 Uso: /libros [tema]\nEjemplo: /libros atributos de Dios")
        return

    if update.effective_chat:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.TYPING,
        )

    prompt = (
        "Recomienda 3 a 5 libros de sana doctrina (Reformada/Puritana/Bautista Reformada) "
        f"sobre: {tema!r}.\n"
        "- Incluye: título, autor, y 1 razón breve.\n"
        "- Evita autores de prosperidad o liberales.\n"
        "- Si recomiendas un libro controversial, adviértelo.\n"
    )

    try:
        text = await gemini_generate(model_flash, prompt)
        await send_safe_html(update, text)
    except Exception as e:
        logger.exception("Error en /libros", exc_info=e)
        await send_safe_html(update, "⚠️ Error consultando la biblioteca.")


@rate_limit(seconds=5)
async def analizar_doctrina(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_in = ""
    if update.message and update.message.reply_to_message:
        text_in = (
            update.message.reply_to_message.text
            or update.message.reply_to_message.caption
            or ""
        )
    elif context.args:
        text_in = " ".join(context.args).strip()

    if not text_in:
        await send_safe_html(update, "⚠️ Responde a un mensaje con /analizar o escribe: /analizar [texto]")
        return

    if update.effective_chat:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.TYPING,
        )

    prompt = (
        "Analiza el texto a la luz de la Escritura.\n"
        "Formato de salida obligatorio:\n"
        "1) Diagnóstico (1–3 frases)\n"
        "2) Errores doctrinales o riesgos (bullets)\n"
        "3) Textos bíblicos relevantes (citas: Libro cap:vers)\n"
        "4) Corrección breve y pastoral\n"
        "5) Preguntas útiles (1–2) si falta contexto\n\n"
        f"TEXTO:\n{text_in}"
    )

    try:
        out = await gemini_generate(model_flash, prompt)
        await send_safe_html(update, out)
    except Exception as e:
        logger.exception("Error en /analizar", exc_info=e)
        await send_safe_html(update, "⚠️ Error en el análisis teológico.")


async def consulta_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id) if update.effective_user else ""
    if user_id != str(CONFIG.owner_id):
        await send_safe_html(update, "⛔ Acceso denegado. Este comando es solo para el administrador.")
        return

    consulta = " ".join(context.args).strip()
    if not consulta:
        await send_safe_html(update, "🧠 Modo Pro (Gemini 3 Pro Preview)\nUso: /pro [pregunta compleja]")
        return

    if update.effective_chat:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.TYPING,
        )

    try:
        out = await gemini_generate(model_pro, consulta)
        await send_safe_html(update, out)
    except Exception as e:
        logger.exception("Error en /pro", exc_info=e)
        await send_safe_html(update, "⚠️ Error en Gemini Pro.")


# -----------------------------
# 6) CHAT HANDLER
# -----------------------------
@rate_limit(seconds=2)
async def manejar_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_type = update.effective_chat.type if update.effective_chat else "private"
    text = update.message.text.strip()

    es_privado = chat_type == "private"
    es_mencion = is_mentioned_in_group(update, context.bot.username)

    # Private: always respond. Groups: ONLY if explicitly @mentioned.
    if not (es_privado or es_mencion):
        return

    if update.effective_chat:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.TYPING,
        )

    prompt = (
        "Responde como asistente teológico reformado, con Biblia como autoridad.\n"
        "Reglas:\n"
        "- Sé directo, pastoral.\n"
        "- Incluye 1–3 textos bíblicos si haces afirmaciones doctrinales.\n"
        "- Si el usuario pide opinión prudencial, marca que es prudencia.\n\n"
        f"Usuario dice/pregunta:\n{text}"
    )

    try:
        out = await gemini_generate(model_flash, prompt)
        await send_safe_html(update, out)
    except Exception as e:
        logger.exception("Error en chat handler", exc_info=e)
        await send_safe_html(update, "⚠️ Hubo un error procesando tu mensaje.")


# -----------------------------
# 7) GLOBAL ERROR HANDLER
# -----------------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text("⚠️ Error interno. Intenta de nuevo en unos segundos.")
        except Exception:
            pass


# -----------------------------
# 8) MAIN
# -----------------------------
def main() -> None:
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = (
        ApplicationBuilder()
        .token(CONFIG.telegram_token)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("libros", recomendar_libros))
    application.add_handler(CommandHandler("analizar", analizar_doctrina))
    application.add_handler(CommandHandler("pro", consulta_pro))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensajes))

    application.add_error_handler(on_error)

    logger.info("🚀 ReformadoAI: Iniciando servicios con Doble Motor...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
