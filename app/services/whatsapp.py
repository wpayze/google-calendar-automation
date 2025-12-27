import json
import os
import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Set

from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse

DB_PATH = os.getenv("SQLITE_DB_PATH", "conversation_state.db")

STATE_IDLE = "IDLE"
STATE_DATE_PICK = "DATE_PICK"
STATE_DATE_FREEFORM = "DATE_FREEFORM"
STATE_WAITING_NAME = "WAITING_NAME"
STATE_WAITING_EMAIL = "WAITING_EMAIL"
STATE_WAITING_ADDRESS = "WAITING_ADDRESS"
STATE_WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
STATE_WAITING_DESCRIPTION = "WAITING_DESCRIPTION"

CALCULATOR_URL = os.getenv(
    "BUDGET_CALCULATOR_URL",
    "Formulario de presupuesto no disponible por el momento.",
)

MENU_TEXT = (
    "👋 ¡Hola! Soy el asistente virtual de *EGM Grupo*.\n\n"
    "Puedo ayudarte con lo siguiente:\n"
    "🛠️ Agendar una visita para tu reforma\n"
    "ℹ️ Conocer más sobre nuestros servicios\n"
    "🧮 Calcular un presupuesto orientativo online\n\n"
    "Responde con:\n"
    "1️⃣ Agendar cita\n"
    "2️⃣ Información\n"
    "3️⃣ Calculadora de presupuesto"
)

INFO_TEXT = (
    "🏗️ *EGM Grupo*\n"
    "Reformas y diseño en Valencia.\n\n"
    "✔️ Presupuestos en 48h\n"
    "✔️ Proyectos a medida\n"
    "✔️ Acompañamiento de principio a fin\n"
)

INVALID_OPTION_TEXT = "❌ Opción no válida. Responde con uno de los números indicados."

VALID_CHOICES: Dict[str, Optional[Set[str]]] = {
    STATE_IDLE: {"1", "2", "3"},
    STATE_DATE_PICK: {"1", "2", "3", "4", "5"},
    STATE_WAITING_CONFIRMATION: {"1", "2", "3"},
    STATE_DATE_FREEFORM: None,        # texto libre: DD-MM-YYYY
    STATE_WAITING_NAME: None,         # texto libre
    STATE_WAITING_EMAIL: None,        # texto libre
    STATE_WAITING_ADDRESS: None,      # texto libre
    STATE_WAITING_DESCRIPTION: None,  # texto libre
}


# -------------------- DB helpers --------------------
def _get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _get_db_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_state (
                phone TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                data_json TEXT NOT NULL,
                stack_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(conversation_state)")}
        if "stack_json" not in cols:
            conn.execute("ALTER TABLE conversation_state ADD COLUMN stack_json TEXT NOT NULL DEFAULT '[]'")
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE conversation_state ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def load_state(phone: str) -> Dict[str, Any]:
    conn = _get_db_conn()
    try:
        cur = conn.execute(
            "SELECT phone, state, data_json FROM conversation_state WHERE phone = ?",
            (phone,),
        )
        row = cur.fetchone()
        if not row:
            return {"state": STATE_IDLE, "data": {}}
        return {"state": row["state"], "data": json.loads(row["data_json"] or "{}")}
    finally:
        conn.close()


def save_state(phone: str, state: str, data: Dict[str, Any]) -> None:
    conn = _get_db_conn()
    try:
        conn.execute(
            """
            INSERT INTO conversation_state (phone, state, data_json, stack_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                state=excluded.state,
                data_json=excluded.data_json,
                stack_json=excluded.stack_json,
                updated_at=excluded.updated_at
            """,
            (
                phone,
                state,
                json.dumps(data, ensure_ascii=False),
                "[]",
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def reset_state(phone: str) -> None:
    save_state(phone, STATE_IDLE, {})


# -------------------- Slot helpers --------------------
def _slot_options(base_day: date) -> List[str]:
    times = [time(10, 0), time(12, 0), time(16, 0)]
    slots: List[str] = []
    for idx, t in enumerate(times):
        dt = datetime.combine(base_day + timedelta(days=idx), t)
        slots.append(dt.strftime("%d-%m-%Y %H:%M"))
    return slots


def _format_slot_pretty(slot: str) -> str:
    try:
        dt = datetime.strptime(slot, "%d-%m-%Y %H:%M")
    except ValueError:
        return slot

    months = [
        "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ]
    return f"{dt.day} de {months[dt.month]} de {dt.year} a las {dt.strftime('%H:%M')}"


def build_menu(resp: MessagingResponse) -> None:
    resp.message(MENU_TEXT)


def build_slot_menu(resp: MessagingResponse, slots: List[str]) -> None:
    formatted = [_format_slot_pretty(s) for s in slots]
    resp.message(
        "📅 *Fechas disponibles*\n\n"
        "Elige el horario que mejor te venga:\n\n"
        f"1️⃣ {formatted[0]}\n"
        f"2️⃣ {formatted[1]}\n"
        f"3️⃣ {formatted[2]}\n\n"
        "4️⃣ 📆 Otra fecha\n"
        "5️⃣ 🔙 Menú principal\n\n"
        "Responde con el número de tu elección."
    )


def parse_ddmmyyyy(text: str) -> Optional[date]:
    try:
        return datetime.strptime(text.strip(), "%d-%m-%Y").date()
    except ValueError:
        return None


# -------------------- Flow helpers --------------------
def xml(resp: MessagingResponse) -> Response:
    return Response(content=str(resp), media_type="application/xml")


def render_state_prompt(resp: MessagingResponse, state: str, data: Dict[str, Any]) -> None:
    if state == STATE_IDLE:
        build_menu(resp)
        return

    if state == STATE_DATE_PICK:
        slots = data.get("slots") or []
        if isinstance(slots, list) and len(slots) >= 3:
            build_slot_menu(resp, slots)
        else:
            build_menu(resp)
        return

    if state == STATE_WAITING_CONFIRMATION:
        resp.message(
            "✅ *Revisa los datos de tu cita:*\n\n"
            f"📅 {_format_slot_pretty(str(data.get('chosen_slot', '')))}\n"
            f"👤 {str(data.get('name', ''))}\n"
            f"📧 {str(data.get('email', ''))}\n"
            f"📍 {str(data.get('address', ''))}\n"
            f"🛠️ {str(data.get('description', ''))}\n\n"
            "¿Todo es correcto?\n\n"
            "1️⃣ Confirmar cita\n"
            "2️⃣ Corregir datos\n"
            "3️⃣ Cancelar cita"
        )
        return

    if state == STATE_DATE_FREEFORM:
        resp.message(
            "📆 Indícanos la fecha que te vendría bien.\n\n"
            "Formato: *DD-MM-YYYY*\n"
            "Ejemplo: 15-01-2026"
        )
        return

    if state == STATE_WAITING_NAME:
        resp.message("¿Cuál es tu *nombre*?")
        return

    if state == STATE_WAITING_EMAIL:
        resp.message("¿Cuál es tu *correo electrónico*?")
        return

    if state == STATE_WAITING_ADDRESS:
        resp.message("📍 Indícanos la *dirección de la vivienda*:")
        return

    if state == STATE_WAITING_DESCRIPTION:
        resp.message(
            "📝 Cuéntanos brevemente qué necesitas.\n\n"
            "Ejemplos:\n"
            "• Reforma integral\n"
            "• Reforma de cocina\n"
            "• Reforma de baño"
        )
        return

    build_menu(resp)


def require_choice(resp: MessagingResponse, state: str, text: str, data: Dict[str, Any]) -> Optional[str]:
    valid = VALID_CHOICES.get(state)
    if valid is None:
        return text  # texto libre

    if text not in valid:
        resp.message(INVALID_OPTION_TEXT)
        render_state_prompt(resp, state, data)
        return None

    return text


# -------------------- Main handler --------------------
def handle_whatsapp_message(from_number: Optional[str], body: Optional[str]) -> Response:
    init_db()

    phone = (from_number or "").strip()
    text = (body or "").strip()

    resp = MessagingResponse()

    session = load_state(phone)
    state = str(session.get("state") or STATE_IDLE)
    data: Dict[str, Any] = session.get("data") or {}

    # IDLE (solo números)
    if state == STATE_IDLE:
        if text not in {"1", "2", "3"}:
            build_menu(resp)
            return xml(resp)

        if text == "1":
            slots = _slot_options(datetime.now().date() + timedelta(days=1))
            save_state(phone, STATE_DATE_PICK, {"slots": slots})
            build_slot_menu(resp, slots)
            return xml(resp)

        if text == "2":
            resp.message(INFO_TEXT)
            reset_state(phone)
            return xml(resp)

        if text == "3":
            resp.message(
                "🧮 Calcula un presupuesto orientativo desde casa:\n\n"
                f"👉 {CALCULATOR_URL}"
            )
            reset_state(phone)
            return xml(resp)

    # DATE_PICK (solo números)
    if state == STATE_DATE_PICK:
        slots = data.get("slots") or []
        if not isinstance(slots, list):
            slots = []

        choice = require_choice(resp, state, text, {"slots": slots})
        if choice is None:
            return xml(resp)

        if choice in {"1", "2", "3"}:
            if len(slots) < 3:
                resp.message("❌ No hay horarios disponibles. Volviendo al menú.")
                reset_state(phone)
                build_menu(resp)
                return xml(resp)

            chosen = slots[int(choice) - 1]
            save_state(phone, STATE_WAITING_NAME, {"chosen_slot": chosen})
            resp.message(
                f"👍 Has elegido:\n"
                f"📅 {_format_slot_pretty(chosen)}\n\n"
                "¿Cuál es tu *nombre*?"
            )
            return xml(resp)

        if choice == "4":
            save_state(phone, STATE_DATE_FREEFORM, {})
            render_state_prompt(resp, STATE_DATE_FREEFORM, {})
            return xml(resp)

        if choice == "5":
            reset_state(phone)
            build_menu(resp)
            return xml(resp)

        resp.message(INVALID_OPTION_TEXT)
        build_slot_menu(resp, slots if isinstance(slots, list) else [])
        return xml(resp)

    # DATE_FREEFORM (texto libre DD-MM-YYYY)
    if state == STATE_DATE_FREEFORM:
        parsed = parse_ddmmyyyy(text)
        if parsed:
            slots = _slot_options(parsed)
            save_state(phone, STATE_DATE_PICK, {"slots": slots})
            build_slot_menu(resp, slots)
            return xml(resp)

        resp.message(
            "❌ Formato inválido.\n"
            "Usa *DD-MM-YYYY*\n"
            "Ejemplo: 15-01-2026"
        )
        render_state_prompt(resp, STATE_DATE_FREEFORM, {})
        return xml(resp)

    # WAITING_NAME (texto libre)
    if state == STATE_WAITING_NAME:
        if not text:
            resp.message("❌ No entendí el nombre. Escríbelo nuevamente.")
            render_state_prompt(resp, STATE_WAITING_NAME, data)
            return xml(resp)

        data["name"] = text
        save_state(phone, STATE_WAITING_EMAIL, data)
        render_state_prompt(resp, STATE_WAITING_EMAIL, data)
        return xml(resp)

    # WAITING_EMAIL (texto libre)
    if state == STATE_WAITING_EMAIL:
        if not text:
            resp.message("❌ No entendí el correo. Escríbelo nuevamente.")
            render_state_prompt(resp, STATE_WAITING_EMAIL, data)
            return xml(resp)

        data["email"] = text
        save_state(phone, STATE_WAITING_ADDRESS, data)
        render_state_prompt(resp, STATE_WAITING_ADDRESS, data)
        return xml(resp)

    # WAITING_ADDRESS (texto libre)
    if state == STATE_WAITING_ADDRESS:
        if not text:
            resp.message("❌ No entendí la dirección. Escríbela nuevamente.")
            render_state_prompt(resp, STATE_WAITING_ADDRESS, data)
            return xml(resp)

        data["address"] = text
        save_state(phone, STATE_WAITING_DESCRIPTION, data)
        render_state_prompt(resp, STATE_WAITING_DESCRIPTION, data)
        return xml(resp)

    # WAITING_DESCRIPTION (texto libre)
    if state == STATE_WAITING_DESCRIPTION:
        if not text:
            resp.message("❌ No entendí la descripción. Escríbela nuevamente.")
            render_state_prompt(resp, STATE_WAITING_DESCRIPTION, data)
            return xml(resp)

        data["description"] = text
        save_state(phone, STATE_WAITING_CONFIRMATION, data)
        render_state_prompt(resp, STATE_WAITING_CONFIRMATION, data)
        return xml(resp)

    # WAITING_CONFIRMATION (solo números)
    if state == STATE_WAITING_CONFIRMATION:
        # soporte para "INFO mode" (opción 2 del menú principal)
        if data.get("info_mode") is True:
            if text not in {"1", "2"}:
                resp.message(INVALID_OPTION_TEXT)
                resp.message(INFO_TEXT + "\n\n" + "Responde:\n1️⃣ Agendar cita\n2️⃣ Menú principal")
                return xml(resp)

            if text == "1":
                slots = _slot_options(datetime.now().date() + timedelta(days=1))
                save_state(phone, STATE_DATE_PICK, {"slots": slots})
                build_slot_menu(resp, slots)
                return xml(resp)

            reset_state(phone)
            build_menu(resp)
            return xml(resp)

        choice = require_choice(resp, state, text, data)
        if choice is None:
            return xml(resp)

        if choice == "1":
            reset_state(phone)
            resp.message(
                "🎉 *¡Cita confirmada!* 🎉\n\n"
                "Gracias por confiar en *EGM Grupo*.\n"
                "Nos pondremos en contacto contigo muy pronto 😊"
            )
            return xml(resp)

        if choice == "2":
            save_state(phone, STATE_WAITING_NAME, {"chosen_slot": data.get("chosen_slot", "")})
            resp.message("🔁 Vamos a corregir los datos.\n¿Cuál es tu nombre?")
            return xml(resp)
        if choice == "3":
            reset_state(phone)
            resp.message("Cita cancelada. Volviendo al menu principal.")
            build_menu(resp)
            return xml(resp)

        resp.message(INVALID_OPTION_TEXT)
        render_state_prompt(resp, STATE_WAITING_CONFIRMATION, data)
        return xml(resp)

    reset_state(phone)
    build_menu(resp)
    return xml(resp)
