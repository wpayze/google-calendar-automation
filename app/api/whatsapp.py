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
    "Bienvenido a EGM Grupo, soy tu asistente virtual.\n"
    "Si estás interesado en una reforma, puedo agendar una cita para que discutamos tu proyecto. "
    "También puedo darte más información de nuestros servicios.\n\n"
    "1) Agendar una cita\n"
    "2) Información\n"
    "3) Calculadora de presupuesto online\n\n"
    "Responde con 1, 2 o 3."
)

INFO_TEXT = (
    "Somos EGM Grupo. Reformas y diseño en Valencia. "
    "Presupuestos en 48h. ¿Quieres agendar una visita?"
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
        # Ensure stack_json and updated_at columns exist for older DBs
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
    """Generate three slots (date+time) starting from base_day, one per day."""
    times = [time(10, 0), time(12, 0), time(16, 0)]
    slots = []
    for idx, t in enumerate(times):
        dt = datetime.combine(base_day + timedelta(days=idx), t)
        slots.append(dt.strftime("%d-%m-%Y %H:%M"))
    return slots


def _format_slot_pretty(slot: str) -> str:
    """Return human friendly slot text like '5 de Noviembre de 2025 a las 10:00'."""
    try:
        dt = datetime.strptime(slot, "%d-%m-%Y %H:%M")
    except ValueError:
        return slot

    months = [
        "",
        "Enero",
        "Febrero",
        "Marzo",
        "Abril",
        "Mayo",
        "Junio",
        "Julio",
        "Agosto",
        "Septiembre",
        "Octubre",
        "Noviembre",
        "Diciembre",
    ]
    month_name = months[dt.month]
    return f"{dt.day} de {month_name} de {dt.year} a las {dt.strftime('%H:%M')}"


def build_menu(resp: MessagingResponse) -> None:
    resp.message(MENU_TEXT)


def build_slot_menu(resp: MessagingResponse, slots: List[str]) -> None:
    formatted = [_format_slot_pretty(s) for s in slots]
    resp.message(
        "Estas son las fechas más próximas disponibles.\n"
        "Elige un horario:\n"
        f"1) {formatted[0]}\n"
        f"2) {formatted[1]}\n"
        f"3) {formatted[2]}\n"
        "4) Otra fecha\n"
        "5) Menú principal\n\n"
        "Responde con 1, 2, 3, 4 o 5."
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

    # Global menu commands
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
                "Te gustaría calcular un presupuesto desde la comodidad de tu casa? "
                f"Prueba nuestro calculador de presupuestos en este enlace: {CALCULATOR_URL}"
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
            resp.message(f"Has elegido: {_format_slot_pretty(chosen)}.\n¿Cuál es tu nombre?")
        elif lower in {"4", "otra", "otra fecha"}:
            save_state(phone, STATE_DATE_FREEFORM, {})
            resp.message("¿En qué fecha te vendría bien? Formato DD-MM-YYYY. Ejemplo: 15-01-2026")
        elif lower in {"5", "menu principal", "menú principal"}:
            reset_state(phone)
            build_menu(resp)
        else:
            resp.message("No entendí. Elige 1, 2, 3, 4 o 5. Envía 0 para menú.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_DATE_FREEFORM:
        parsed = parse_ddmmyyyy(text)
        if parsed:
            slots = _slot_options(parsed)
            save_state(phone, STATE_DATE_PICK, {"slots": slots})
            build_slot_menu(resp, slots)
        else:
            resp.message("Formato inválido. Usa DD-MM-YYYY. Ejemplo: 15-01-2026. Envía 0 para menú.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_NAME:
        if text:
            data["name"] = text
            save_state(phone, STATE_WAITING_EMAIL, data)
            resp.message(f"Gracias {text}, ¿cuál es tu correo electrónico?")
        else:
            resp.message("No entendí el nombre. Indícalo nuevamente, por favor.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_EMAIL:
        if text:
            data["email"] = text
            save_state(phone, STATE_WAITING_ADDRESS, data)
            resp.message("Anota la dirección de la vivienda:")
        else:
            resp.message("No entendí el correo. Escríbelo nuevamente, por favor.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_ADDRESS:
        if text:
            data["address"] = text
            save_state(phone, STATE_WAITING_DESCRIPTION, data)
            resp.message(
                "Dinos brevemente qué necesitas (ejemplo: reformar mi piso, mi cocina, mi baño)."
            )
        else:
            resp.message("No entendí la dirección. Escríbela nuevamente, por favor.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_DESCRIPTION:
        if text:
            data["description"] = text
            slot = data.get("chosen_slot", "")
            name = data.get("name", "")
            email = data.get("email", "")
            address = data.get("address", "")
            description = data.get("description", "")
            resp.message(
                "Confirma los datos:\n"
                f"Fecha: {_format_slot_pretty(slot)}\n"
                f"Nombre: {name}\n"
                f"Correo: {email}\n"
                f"Dirección: {address}\n"
                f"Proyecto: {description}\n"
                "1. Confirmar cita\n"
                "2. Corregir datos"
            )
            save_state(phone, STATE_WAITING_CONFIRMATION, data)
        else:
            resp.message("No entendí la descripción. Escríbela nuevamente, por favor.")
        return Response(content=str(resp), media_type="application/xml")

    if state == STATE_WAITING_CONFIRMATION:
        slot = data.get("chosen_slot", "")
        name = data.get("name", "")
        email = data.get("email", "")
        address = data.get("address", "")
        description = data.get("description", "")
        if lower in {"1", "confirmar", "confirmar cita"}:
            reset_state(phone)
            resp.message(
                f"Cita confirmada para {_format_slot_pretty(slot)}.\n"
                f"Nombre: {name}\n"
                f"Correo: {email}\n"
                f"Dirección: {address}\n"
                f"Proyecto: {description}\n"
                "Gracias."
            )
        elif lower in {"2", "corregir", "corregir datos"}:
            updated_data = {"chosen_slot": slot}
            save_state(phone, STATE_WAITING_NAME, updated_data)
            resp.message("Vamos a corregir los datos. ¿Cuál es tu nombre?")
        else:
            resp.message("Responde 1 para confirmar o 2 para corregir los datos.")
        return Response(content=str(resp), media_type="application/xml")

    # Fallback
    reset_state(phone)
    build_menu(resp)
    return Response(content=str(resp), media_type="application/xml")
