import json
import os
import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional

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

CALCULATOR_URL = os.getenv("BUDGET_CALCULATOR_URL", "https://example.com")

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
    "✔️ Acompañamiento de principio a fin\n\n"
    "¿Quieres que agendemos una visita? 😊"
)

MENU_COMMANDS = {"0", "menu", "menú", "principal", "menu principal", "menú principal"}


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


def load_state(phone: str) -> Dict[str, Optional[str]]:
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


def save_state(phone: str, state: str, data: Dict[str, str]) -> None:
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
    slots = []
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
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
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


# -------------------- Main handler --------------------
def handle_whatsapp_message(from_number: Optional[str], body: Optional[str]) -> Response:
    init_db()

    phone = (from_number or "").strip()
    text = (body or "").strip()
    lower = text.lower()

    resp = MessagingResponse()

    if lower in MENU_COMMANDS:
        reset_state(phone)
        build_menu(resp)
        return Response(content=str(resp), media_type="application/xml")

    session = load_state(phone)
    state = session["state"]
    data: Dict[str, str] = session.get("data", {}) or {}

    if state == STATE_IDLE:
        if lower in {"1", "reservar", "reserva", "cita", "agendar"}:
            slots = _slot_options(datetime.now().date() + timedelta(days=1))
            save_state(phone, STATE_DATE_PICK, {"slots": slots})
            build_slot_menu(resp, slots)
        elif lower in {"2", "info", "informacion", "información"}:
            resp.message(INFO_TEXT)
            reset_state(phone)
        elif lower in {"3", "calculadora", "presupuesto"}:
            resp.message(
                "🧮 Calcula un presupuesto orientativo desde casa:\n\n"
                f"👉 {CALCULATOR_URL}"
            )
            reset_state(phone)
        else:
            build_menu(resp)
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_DATE_PICK:
        slots = data.get("slots") or []
        if lower in {"1", "2", "3"} and len(slots) >= 3:
            chosen = slots[int(lower) - 1]
            save_state(phone, STATE_WAITING_NAME, {"chosen_slot": chosen})
            resp.message(
                f"👍 Has elegido:\n"
                f"📅 {_format_slot_pretty(chosen)}\n\n"
                "¿Cuál es tu *nombre*?"
            )
        elif lower in {"4", "otra", "otra fecha"}:
            save_state(phone, STATE_DATE_FREEFORM, {})
            resp.message(
                "📆 Perfecto.\n"
                "Indícanos la fecha que te vendría bien.\n\n"
                "Formato: *DD-MM-YYYY*\n"
                "Ejemplo: 15-01-2026"
            )
        elif lower in {"5", "menu principal", "menú principal"}:
            reset_state(phone)
            build_menu(resp)
        else:
            resp.message("❌ No entendí tu respuesta. Elige una opción válida.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_DATE_FREEFORM:
        parsed = parse_ddmmyyyy(text)
        if parsed:
            slots = _slot_options(parsed)
            save_state(phone, STATE_DATE_PICK, {"slots": slots})
            build_slot_menu(resp, slots)
        else:
            resp.message(
                "❌ Formato inválido.\n"
                "Usa *DD-MM-YYYY*\n"
                "Ejemplo: 15-01-2026"
            )
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_NAME:
        if text:
            data["name"] = text
            save_state(phone, STATE_WAITING_EMAIL, data)
            resp.message(
                f"Encantado, {text} 😊\n"
                "¿Cuál es tu *correo electrónico*?"
            )
        else:
            resp.message("❌ No entendí el nombre. Escríbelo nuevamente.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_EMAIL:
        if text:
            data["email"] = text
            save_state(phone, STATE_WAITING_ADDRESS, data)
            resp.message("📍 Indícanos la *dirección de la vivienda*:")
        else:
            resp.message("❌ No entendí el correo. Escríbelo nuevamente.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_ADDRESS:
        if text:
            data["address"] = text
            save_state(phone, STATE_WAITING_DESCRIPTION, data)
            resp.message(
                "📝 Cuéntanos brevemente qué necesitas.\n\n"
                "Ejemplos:\n"
                "• Reforma integral\n"
                "• Reforma de cocina\n"
                "• Reforma de baño"
            )
        else:
            resp.message("❌ No entendí la dirección. Escríbela nuevamente.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_DESCRIPTION:
        if text:
            data["description"] = text
            save_state(phone, STATE_WAITING_CONFIRMATION, data)

            resp.message(
                "✅ *Revisa los datos de tu cita:*\n\n"
                f"📅 {_format_slot_pretty(data.get('chosen_slot', ''))}\n"
                f"👤 {data.get('name', '')}\n"
                f"📧 {data.get('email', '')}\n"
                f"📍 {data.get('address', '')}\n"
                f"🛠️ {data.get('description', '')}\n\n"
                "¿Todo es correcto?\n\n"
                "1️⃣ Confirmar cita\n"
                "2️⃣ Corregir datos"
            )
        else:
            resp.message("❌ No entendí la descripción. Escríbela nuevamente.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_CONFIRMATION:
        if lower in {"1", "confirmar", "confirmar cita"}:
            reset_state(phone)
            resp.message(
                "🎉 *¡Cita confirmada!* 🎉\n\n"
                "Gracias por confiar en *EGM Grupo*.\n"
                "Nos pondremos en contacto contigo muy pronto 😊"
            )
        elif lower in {"2", "corregir", "corregir datos"}:
            save_state(phone, STATE_WAITING_NAME, {"chosen_slot": data.get("chosen_slot", "")})
            resp.message("🔁 Vamos a corregir los datos.\n¿Cuál es tu nombre?")
        else:
            resp.message("Responde 1️⃣ para confirmar o 2️⃣ para corregir.")
        return Response(content=str(resp), media_type="application/xml")

    reset_state(phone)
    build_menu(resp)
    return Response(content=str(resp), media_type="application/xml")
