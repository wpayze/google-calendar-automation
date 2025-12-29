import os
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Set

from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse

from app.services.helpers.whatsapp_db import (
    init_db,
    load_state,
    reset_state,
    save_state,
)
from app.services.reservation_service import ReservationService

STATE_IDLE = "IDLE"
STATE_DATE_PICK = "DATE_PICK"
STATE_DATE_FREEFORM = "DATE_FREEFORM"
STATE_WAITING_NAME = "WAITING_NAME"
STATE_WAITING_EMAIL = "WAITING_EMAIL"
STATE_WAITING_ADDRESS = "WAITING_ADDRESS"
STATE_WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
STATE_WAITING_DESCRIPTION = "WAITING_DESCRIPTION"

NAME_MIN_LEN = 3
NAME_MAX_LEN = 60
ADDRESS_MAX_LEN = 120
DESCRIPTION_MAX_LEN = 300
_reservation_service: Optional[ReservationService] = None

CALCULATOR_URL = os.getenv(
    "BUDGET_CALCULATOR_URL",
    "⚠️ El formulario de presupuesto no está disponible por el momento.",
)

MENU_TEXT = (
    "👋 ¡Hola! Soy el asistente virtual de *EGM Grupo*.\n\n"
    "¿En qué puedo ayudarte hoy?\n\n"
    "1️⃣ Agendar una visita para tu reforma\n"
    "2️⃣ Información sobre nuestros servicios\n"
    "3️⃣ Calculadora de presupuesto online\n\n"
    "Responde con 1, 2 o 3."
)

INFO_TEXT = (
    "🏗️ *EGM Grupo*\n"
    "Reformas y diseño en Valencia.\n\n"
    "✨ Lo que ofrecemos:\n"
    "✔️ Presupuesto en 48h\n"
    "✔️ Proyectos a medida\n"
    "✔️ Acompañamiento de principio a fin\n\n"
    "Si quieres, puedo ayudarte a agendar una visita 📅"
)

INVALID_OPTION_TEXT = "❌ Opción no válida. Responde con uno de los números del menú."

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


# -------------------- Slot helpers --------------------
def _slot_options(base_day: date) -> List[str]:
    times = [time(10, 0), time(12, 0), time(16, 0)]
    slots: List[str] = []
    for idx, t in enumerate(times):
        dt = datetime.combine(base_day + timedelta(days=idx), t)
        slots.append(dt.strftime("%d-%m-%Y %H:%M"))
    return slots


def _get_reservation_service() -> Optional[ReservationService]:
    global _reservation_service
    if _reservation_service is not None:
        return _reservation_service
    try:
        _reservation_service = ReservationService()
    except Exception:
        _reservation_service = None
    return _reservation_service


async def _fetch_slots(from_date: Optional[date]) -> List[str]:
    service = _get_reservation_service()
    if service is None:
        raise ValueError("😕 Ahora mismo el servicio de citas no está disponible. Intenta más tarde.")

    args: Dict[str, Any] = {}
    if from_date:
        args["desde"] = from_date.isoformat()

    result = await service.list_next_slots(args)

    slots_raw = result.get("available_slots") or []
    slots: List[str] = []
    for item in slots_raw:
        start_iso = item.get("slot_start_iso")
        if not start_iso:
            continue
        try:
            dt = datetime.fromisoformat(start_iso)
            slots.append(dt.strftime("%d-%m-%Y %H:%M"))
        except Exception:
            continue

    if not slots:
        raise ValueError("😔 No hay horarios disponibles en este momento. Prueba más tarde.")

    return slots[:3]


def _parse_slot_ddmmyyyy_hhmm(slot: str) -> Optional[tuple[str, str]]:
    """
    Convierte 'DD-MM-YYYY HH:MM' en (date_iso, time_hhmm).
    """
    try:
        dt = datetime.strptime(slot, "%d-%m-%Y %H:%M")
    except ValueError:
        return None
    return dt.date().isoformat(), dt.strftime("%H:%M")


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
        "📅 *Horarios disponibles*\n\n"
        "Elige el horario que mejor te venga:\n\n"
        f"1️⃣ {formatted[0]}\n"
        f"2️⃣ {formatted[1]}\n"
        f"3️⃣ {formatted[2]}\n\n"
        "4️⃣ 📆 Elegir otra fecha\n"
        "5️⃣ 🔙 Volver al menú principal\n\n"
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
            f"📅 *Fecha:* {_format_slot_pretty(str(data.get('chosen_slot', '')))}\n"
            f"👤 *Nombre:* {str(data.get('name', ''))}\n"
            f"📧 *Email:* {str(data.get('email', ''))}\n"
            f"📍 *Dirección:* {str(data.get('address', ''))}\n"
            f"🛠️ *Detalles:* {str(data.get('description', ''))}\n\n"
            "¿Todo está correcto?\n\n"
            "1️⃣ ✅ Confirmar cita\n"
            "2️⃣ ✏️ Corregir datos\n"
            "3️⃣ 🚫 Cancelar"
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
        resp.message("👤 ¿Cuál es tu *nombre*?")
        return

    if state == STATE_WAITING_EMAIL:
        resp.message("📧 ¿Cuál es tu *correo electrónico*? (Ej: nombre@correo.com)")
        return

    if state == STATE_WAITING_ADDRESS:
        resp.message("📍 Indícanos la *dirección de la vivienda* (calle y número):")
        return

    if state == STATE_WAITING_DESCRIPTION:
        resp.message(
            "📝 Cuéntanos brevemente qué necesitas.\n\n"
            "Ejemplos:\n"
            "• Quiero una reforma integral de mi vivienda\n"
            "• Necesito reformar la cocina y el baño de mi piso\n"
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
async def handle_whatsapp_message(from_number: Optional[str], body: Optional[str]) -> Response:
    init_db()

    phone = (from_number or "").strip()
    text = (body or "").strip()

    resp = MessagingResponse()

    session = load_state(phone, STATE_IDLE)
    state = str(session.get("state") or STATE_IDLE)
    data: Dict[str, Any] = session.get("data") or {}

    # IDLE (solo números)
    if state == STATE_IDLE:
        if text not in {"1", "2", "3"}:
            if data.get("menu_shown"):
                resp.message(INVALID_OPTION_TEXT)
            else:
                data["menu_shown"] = True
                save_state(phone, STATE_IDLE, data)
            build_menu(resp)
            return xml(resp)

        if text == "1":
            try:
                slots = await _fetch_slots(datetime.now().date() + timedelta(days=1))
            except Exception as exc:
                resp.message(str(exc) or "😕 Ahora mismo el servicio de citas no está disponible. Intenta más tarde.")
                reset_state(phone, STATE_IDLE)
                build_menu(resp)
                return xml(resp)
            save_state(phone, STATE_DATE_PICK, {"slots": slots})
            build_slot_menu(resp, slots)
            return xml(resp)

        if text == "2":
            resp.message(INFO_TEXT)
            reset_state(phone, STATE_IDLE)
            return xml(resp)

        if text == "3":
            resp.message(
                "🧮 *Calculadora de presupuesto*\n\n"
                "Calcula un presupuesto orientativo desde casa:\n"
                f"👉 {CALCULATOR_URL}\n\n"
                "Respóndeme cualquier texto para volver al menú principal."
            )
            reset_state(phone, STATE_IDLE)
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
                resp.message("😔 No hay horarios disponibles. Volviendo al menú principal.")
                reset_state(phone, STATE_IDLE)
                build_menu(resp)
                return xml(resp)

            chosen = slots[int(choice) - 1]
            save_state(phone, STATE_WAITING_NAME, {"chosen_slot": chosen})
            resp.message(
                "✅ Perfecto, has elegido:\n"
                f"📅 {_format_slot_pretty(chosen)}\n\n"
                "👤 ¿Cuál es tu *nombre*?"
            )
            return xml(resp)

        if choice == "4":
            save_state(phone, STATE_DATE_FREEFORM, {})
            render_state_prompt(resp, STATE_DATE_FREEFORM, {})
            return xml(resp)

        if choice == "5":
            reset_state(phone, STATE_IDLE)
            build_menu(resp)
            return xml(resp)

        resp.message(INVALID_OPTION_TEXT)
        build_slot_menu(resp, slots if isinstance(slots, list) else [])
        return xml(resp)

    # DATE_FREEFORM (texto libre DD-MM-YYYY)
    if state == STATE_DATE_FREEFORM:
        parsed = parse_ddmmyyyy(text)
        if parsed:
            if parsed < datetime.now().date():
                resp.message("❌ La fecha debe ser *hoy o futura*. Inténtalo de nuevo.")
                render_state_prompt(resp, STATE_DATE_FREEFORM, {})
                return xml(resp)
            if parsed > (datetime.now().date() + timedelta(days=90)):
                resp.message("❌ Solo puedo agendar hasta *3 meses* desde hoy. Prueba con otra fecha.")
                render_state_prompt(resp, STATE_DATE_FREEFORM, {})
                return xml(resp)

            try:
                slots = await _fetch_slots(parsed)
            except Exception as exc:
                resp.message(str(exc) or "😕 Ahora mismo el servicio de citas no está disponible. Intenta más tarde.")
                reset_state(phone, STATE_IDLE)
                build_menu(resp)
                return xml(resp)
            save_state(phone, STATE_DATE_PICK, {"slots": slots})
            build_slot_menu(resp, slots)
            return xml(resp)

        resp.message(
            "❌ Formato incorrecto.\n"
            "Usa *DD-MM-YYYY*\n"
            "Ejemplo: 15-01-2026"
        )
        render_state_prompt(resp, STATE_DATE_FREEFORM, {})
        return xml(resp)

    # WAITING_NAME (texto libre)
    if state == STATE_WAITING_NAME:
        if not text:
            resp.message("❌ No lo he entendido. ¿Puedes escribir tu nombre de nuevo?")
            render_state_prompt(resp, STATE_WAITING_NAME, data)
            return xml(resp)

        if not (NAME_MIN_LEN <= len(text) <= NAME_MAX_LEN):
            resp.message(f"❌ El nombre debe tener entre {NAME_MIN_LEN} y {NAME_MAX_LEN} caracteres.")
            render_state_prompt(resp, STATE_WAITING_NAME, data)
            return xml(resp)

        data["name"] = text
        save_state(phone, STATE_WAITING_EMAIL, data)
        render_state_prompt(resp, STATE_WAITING_EMAIL, data)
        return xml(resp)

    # WAITING_EMAIL (texto libre)
    if state == STATE_WAITING_EMAIL:
        if not text:
            resp.message("❌ No lo he entendido. ¿Puedes escribir tu correo de nuevo?")
            render_state_prompt(resp, STATE_WAITING_EMAIL, data)
            return xml(resp)

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
            resp.message("❌ Ese correo no parece válido. Intenta de nuevo.")
            render_state_prompt(resp, STATE_WAITING_EMAIL, data)
            return xml(resp)

        data["email"] = text
        save_state(phone, STATE_WAITING_ADDRESS, data)
        render_state_prompt(resp, STATE_WAITING_ADDRESS, data)
        return xml(resp)

    # WAITING_ADDRESS (texto libre)
    if state == STATE_WAITING_ADDRESS:
        if not text:
            resp.message("❌ No lo he entendido. ¿Puedes escribir la dirección de nuevo?")
            render_state_prompt(resp, STATE_WAITING_ADDRESS, data)
            return xml(resp)

        if len(text) > ADDRESS_MAX_LEN:
            resp.message(f"❌ Dirección demasiado larga. Máximo {ADDRESS_MAX_LEN} caracteres.")
            render_state_prompt(resp, STATE_WAITING_ADDRESS, data)
            return xml(resp)

        data["address"] = text
        save_state(phone, STATE_WAITING_DESCRIPTION, data)
        render_state_prompt(resp, STATE_WAITING_DESCRIPTION, data)
        return xml(resp)

    # WAITING_DESCRIPTION (texto libre)
    if state == STATE_WAITING_DESCRIPTION:
        if not text:
            resp.message("❌ No lo he entendido. ¿Puedes describirlo de nuevo, por favor?")
            render_state_prompt(resp, STATE_WAITING_DESCRIPTION, data)
            return xml(resp)

        if len(text) > DESCRIPTION_MAX_LEN:
            resp.message(f"❌ Descripción demasiado larga. Máximo {DESCRIPTION_MAX_LEN} caracteres.")
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
                resp.message(INFO_TEXT + "\n\n" + "Responde:\n1️⃣ 🛠️ Agendar cita\n2️⃣ 🔙 Menú principal")
                return xml(resp)

            if text == "1":
                try:
                    slots = await _fetch_slots(datetime.now().date() + timedelta(days=1))
                except Exception as exc:
                    resp.message(str(exc) or "😕 Ahora mismo el servicio de citas no está disponible. Intenta más tarde.")
                    reset_state(phone, STATE_IDLE)
                    build_menu(resp)
                    return xml(resp)
                save_state(phone, STATE_DATE_PICK, {"slots": slots})
                build_slot_menu(resp, slots)
                return xml(resp)

            reset_state(phone, STATE_IDLE)
            build_menu(resp)
            return xml(resp)

        choice = require_choice(resp, state, text, data)
        if choice is None:
            return xml(resp)

        if choice == "1":
            slot_raw = data.get("chosen_slot", "")
            parsed_slot = _parse_slot_ddmmyyyy_hhmm(str(slot_raw))
            service = _get_reservation_service()
            if parsed_slot is None or service is None:
                reset_state(phone, STATE_IDLE)
                resp.message("😕 No pude crear la reserva. Intenta de nuevo más tarde.")
                build_menu(resp)
                return xml(resp)

            date_str, time_str = parsed_slot
            try:
                result = await service.create_reservation(
                    {
                        "date": date_str,
                        "time": time_str,
                        "name": data.get("name", "Invitado"),
                        "customer_number": phone or "No proporcionado",
                        "reforma": data.get("description", "No especificada"),
                    }
                )
                created = result.get("created", False)
                message = result.get("message") or "Reserva registrada."
            except Exception as exc:
                created = False
                message = str(exc) or "Error al crear la reserva."

            reset_state(phone, STATE_IDLE)
            if created:
                resp.message(
                    "🎉 *¡Cita confirmada!* 🎉\n"
                    f"{message}\n\n"
                    "Gracias por confiar en *EGM Grupo* 🙌\n"
                    "Nos pondremos en contacto contigo muy pronto 😊"
                )
            else:
                resp.message(f"😕 No pude crear la reserva: {message}")
                build_menu(resp)
            return xml(resp)

        if choice == "2":
            save_state(phone, STATE_WAITING_NAME, {"chosen_slot": data.get("chosen_slot", "")})
            resp.message("🔁 Perfecto, vamos a corregir los datos.\n\n👤 ¿Cuál es tu *nombre*?")
            return xml(resp)
        if choice == "3":
            reset_state(phone, STATE_IDLE)
            resp.message("🚫 Cita cancelada. Te devuelvo al menú principal.")
            build_menu(resp)
            return xml(resp)

        resp.message(INVALID_OPTION_TEXT)
        render_state_prompt(resp, STATE_WAITING_CONFIRMATION, data)
        return xml(resp)

    reset_state(phone, STATE_IDLE)
    build_menu(resp)
    return xml(resp)
