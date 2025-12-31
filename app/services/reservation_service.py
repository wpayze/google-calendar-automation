import os
from datetime import datetime, timedelta, timezone, time
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.payload_cleaner import CleanPayload
from app.services.business_hours import BUSINESS_HOURS, MORNING_END
from app.enums import MONTH_NAMES, HOUR_WORDS

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
DEFAULT_TOP_N = 5
DEFAULT_DURATION_MINUTES = 60
DEFAULT_TIMEZONE = "Europe/Madrid"
MAX_SLOT_SEARCH_DAYS = 14
SUGGESTION_MEMORY_TTL = timedelta(minutes=10)
_SUGGESTION_MEMORY: Dict[str, List[Tuple[str, datetime]]] = {}


def _align_to_slot(dt: datetime, slot_minutes: int) -> datetime:
    """Round up to the next slot boundary."""
    dt = dt.replace(second=0, microsecond=0)
    minute_total = dt.hour * 60 + dt.minute
    remainder = minute_total % slot_minutes
    if remainder == 0:
        return dt
    next_minutes = minute_total - remainder + slot_minutes
    hours, minutes = divmod(next_minutes, 60)
    day_increment = hours // 24
    aligned = dt.replace(hour=hours % 24, minute=minutes, second=0, microsecond=0)
    if day_increment:
        aligned = aligned + timedelta(days=day_increment)
    return aligned


def _localize(dt: datetime, tz_str: str) -> datetime:
    """Return datetime in target tz, falling back gracefully."""
    try:
        target_tz = ZoneInfo(tz_str)
    except Exception:
        target_tz = dt.tzinfo or timezone.utc
    return dt.astimezone(target_tz) if dt.tzinfo else dt.replace(tzinfo=target_tz)


def _time_phrase(localized: datetime) -> Tuple[str, str, int, int]:
    """Return (time_phrase, period, hour_12, minute) for a localized datetime."""
    hour_24 = localized.hour
    minute = localized.minute
    hour_12 = hour_24 % 12 or 12

    if hour_24 < 6:
        period = "de la madrugada"
    elif hour_24 < 12:
        period = "de la mañana"
    elif hour_24 < 20:
        period = "de la tarde"
    else:
        period = "de la noche"

    if minute == 0:
        time_phrase = HOUR_WORDS.get(hour_12, f"{hour_12}")
    elif minute == 30:
        time_phrase = f"{HOUR_WORDS.get(hour_12, hour_12)} y media"
    elif minute == 15:
        time_phrase = f"{HOUR_WORDS.get(hour_12, hour_12)} y cuarto"
    elif minute == 45:
        next_hour_12 = (hour_12 % 12) + 1
        next_hour_word = HOUR_WORDS.get(next_hour_12, f"{next_hour_12}")
        time_phrase = f"{next_hour_word} menos cuarto"
    else:
        time_phrase = f"{hour_12:02d}:{minute:02d}"

    return time_phrase, period, hour_12, minute


def _slot_speech(dt: datetime, tz_str: str) -> str:
    """Human-friendly Spanish phrase for a slot start."""
    localized = _localize(dt, tz_str)
    time_phrase, period, hour_12, _ = _time_phrase(localized)
    preposition = "a la" if hour_12 == 1 else "a las"
    return f"{localized.day} de {MONTH_NAMES[localized.month - 1]} {preposition} {time_phrase} {period}"


def _day_label(dt: datetime, tz_str: str) -> str:
    """Return '13 de enero' for the given datetime in tz."""
    localized = _localize(dt, tz_str)
    return f"{localized.day} de {MONTH_NAMES[localized.month - 1]}"


def _time_only_speech(dt: datetime, tz_str: str) -> str:
    """Time phrase without the date (e.g., 'tres de la tarde')."""
    localized = _localize(dt, tz_str)
    time_phrase, period, _, _ = _time_phrase(localized)
    return f"{time_phrase} {period}"


def _intervals_for_date(current_date: datetime, block: str) -> List[Tuple[datetime, datetime]]:
    """Return working intervals for a date, filtered by block if provided."""
    tzinfo = current_date.tzinfo
    raw_intervals = BUSINESS_HOURS.get(current_date.weekday(), [])

    def block_filter(start_t, end_t) -> bool:
        if not block:
            return True
        if block in {"morning", "manana", "mañana"}:
            return start_t < MORNING_END
        if block in {"afternoon", "tarde"}:
            return start_t >= MORNING_END
        return True

    intervals: List[Tuple[datetime, datetime]] = []
    for start_t, end_t in raw_intervals:
        if not block_filter(start_t, end_t):
            continue
        start_dt = datetime.combine(current_date.date(), start_t, tzinfo=tzinfo)
        end_dt = datetime.combine(current_date.date(), end_t, tzinfo=tzinfo)
        intervals.append((start_dt, end_dt))
    return intervals


class ReservationService:
    def __init__(self) -> None:
        self.calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
        self.service = self._build_calendar_service()

    async def dispatch(self, payload: CleanPayload) -> Dict[str, Any]:
        tool = payload.tool
        args = payload.arguments

        try:
            if tool == "check_availability":
                return await self.check_availability(args)
            if tool == "create_reservation":
                return await self.create_reservation(args)
            if tool == "list_next_slots":
                return await self.list_next_slots(args)
            if tool == "ping":
                return {"pong": True}
        except ValueError as exc:
            return {"error": str(exc)}
        except HttpError as exc:
            return {"error": f"Google API error: {exc}"}

        return {"error": "Unhandled tool"}

    def _build_calendar_service(self):
        required_keys = [
            "GOOGLE_PROJECT_ID",
            "GOOGLE_PRIVATE_KEY_ID",
            "GOOGLE_PRIVATE_KEY",
            "GOOGLE_SERVICE_ACCOUNT_EMAIL",
        ]
        missing = [key for key in required_keys if not os.getenv(key)]
        if missing:
            raise ValueError(f"Missing Google credential env vars: {missing}")
        if not self.calendar_id:
            raise ValueError("Missing GOOGLE_CALENDAR_ID environment variable.")

        private_key = os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")

        info = {
            "type": "service_account",
            "project_id": os.getenv("GOOGLE_PROJECT_ID"),
            "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"),
            "private_key": private_key,
            "client_email": os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL"),
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "token_uri": "https://oauth2.googleapis.com/token",
        }

        credentials = Credentials.from_service_account_info(
            info, scopes=CALENDAR_SCOPES
        )
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def _extract_time_window(self, arguments: Dict[str, Any]) -> Tuple[str, str, str]:
        date_str = arguments.get("date")
        time_str = arguments.get("time")
        duration_minutes = DEFAULT_DURATION_MINUTES
        tz_str = DEFAULT_TIMEZONE

        if not date_str or not time_str:
            raise ValueError("Both 'date' (YYYY-MM-DD) and 'time' (HH:MM) are required.")

        try:
            tzinfo = ZoneInfo(tz_str)
        except Exception:
            tzinfo = timezone.utc
            tz_str = "UTC"

        start_naive = datetime.fromisoformat(f"{date_str}T{time_str}")
        start = start_naive.replace(tzinfo=tzinfo)
        end = start + timedelta(minutes=duration_minutes)

        return start.isoformat(), end.isoformat(), tz_str

    def _ensure_business_hours(self, date_str: str, time_str: str) -> None:
        if not date_str or not time_str:
            return
        tzinfo = ZoneInfo(DEFAULT_TIMEZONE)
        dt = datetime.fromisoformat(f"{date_str}T{time_str}").replace(tzinfo=tzinfo)
        intervals = _intervals_for_date(dt, block="")
        if not intervals:
            raise ValueError("Outside business hours.")
        inside = any(start <= dt < end for start, end in intervals)
        if not inside:
            raise ValueError("Outside business hours.")


    async def check_availability(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Consulta disponibilidad para un slot específico y devuelve huecos del dia."""
        self._ensure_business_hours(
            arguments.get("date"),
            arguments.get("time"),
        )
        args_with_tz = {**arguments, "timezone": DEFAULT_TIMEZONE}
        start_iso, end_iso, tz_str = self._extract_time_window(args_with_tz)

        tzinfo = ZoneInfo(tz_str)
        day_dt = datetime.fromisoformat(f"{arguments.get('date')}T00:00:00").replace(tzinfo=tzinfo)
        intervals = _intervals_for_date(day_dt, block="")
        if not intervals:
            return {
                "available": False,
                "busy": [],
                "available_slots": [],
                "slots_in_day": False,
                "day": _day_label(day_dt, tz_str),
            }

        day_start = intervals[0][0]
        day_end = intervals[-1][1]

        body = {
            "timeMin": day_start.isoformat(),
            "timeMax": day_end.isoformat(),
            "items": [{"id": self.calendar_id}],
            "timeZone": tz_str,
        }
        response = self.service.freebusy().query(body=body).execute()
        busy_slots = response.get("calendars", {}).get(self.calendar_id, {}).get("busy", [])

        requested_start = datetime.fromisoformat(start_iso)
        requested_end = datetime.fromisoformat(end_iso)
        requested_available = True
        for busy in busy_slots:
            busy_start = datetime.fromisoformat(busy["start"])
            busy_end = datetime.fromisoformat(busy["end"])
            if requested_start < busy_end and requested_end > busy_start:
                requested_available = False
                break

        slots_for_day: List[Dict[str, Any]] = []
        for start_dt, end_dt in intervals:
            cursor = _align_to_slot(start_dt, DEFAULT_DURATION_MINUTES)
            while cursor + timedelta(minutes=DEFAULT_DURATION_MINUTES) <= end_dt:
                candidate_start = cursor
                candidate_end = cursor + timedelta(minutes=DEFAULT_DURATION_MINUTES)

                overlap = False
                for busy in busy_slots:
                    busy_start = datetime.fromisoformat(busy["start"])
                    busy_end = datetime.fromisoformat(busy["end"])
                    if candidate_start < busy_end and candidate_end > busy_start:
                        overlap = True
                        break

                if not overlap:
                    slots_for_day.append(
                        {
                            "slot_start_iso": candidate_start.isoformat(),
                            "slot_end_iso": candidate_end.isoformat(),
                            "slot_speech": _slot_speech(candidate_start, tz_str),
                        }
                    )

                cursor = candidate_end

        slots_for_day_sorted = sorted(slots_for_day, key=lambda s: s["slot_start_iso"])
        return {
            "available": requested_available,
            # "busy": busy_slots,
            "day": _day_label(day_dt, tz_str),
            "available_slots": [_time_only_speech(datetime.fromisoformat(slot["slot_start_iso"]), tz_str) for slot in slots_for_day_sorted],
            "slots_in_day": len(slots_for_day_sorted) > 0,
        }

    async def create_reservation(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Crea una reserva y confirma si está dentro de horario disponible y laboral."""
        self._ensure_business_hours(
            arguments.get("date"),
            arguments.get("time"),
        )
        availability = await self.check_availability(arguments)
        if not availability.get("available", False):
            return {"created": False, "reason": "not_available", "busy": availability.get("busy")}

        args_with_tz = {**arguments, "timezone": DEFAULT_TIMEZONE}
        start_iso, end_iso, tz_str = self._extract_time_window(args_with_tz)
        name = arguments.get("name", "Invitado")
        customer_number = arguments.get("customer_number", "No proporcionado")
        reforma = arguments.get("reforma", "No especificada")
        summary = f"Cita agendada con {name}"
        description = (
            f"Telefono: {customer_number}\n"
            f"Nombre: {name}\n"
            f"Fecha: {arguments.get('date')}\n"
            f"Hora: {arguments.get('time')}\n"
            f"Duracion: 60 minutos\n"
            f"Zona horaria: {tz_str}\n"
            f"Al usuario le interesa {reforma}"
        )

        event_body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_iso, "timeZone": tz_str},
            "end": {"dateTime": end_iso, "timeZone": tz_str},
        }

        self.service.events().insert(calendarId=self.calendar_id, body=event_body).execute()
        return {
            "created": True,
            "message": f"Reserva confirmada para {arguments.get('date')} a las {arguments.get('time')} ({tz_str})",
        }

    def _recent_suggestions(self, user_key: str, now: datetime) -> List[str]:
        """Return slot_start_iso values recently suggested for this user."""
        if not user_key:
            return []
        entries = _SUGGESTION_MEMORY.get(user_key, [])
        return [slot for slot, ts in entries if now - ts < SUGGESTION_MEMORY_TTL]

    def _remember_suggestion(self, user_key: str, slot_iso: str, now: datetime) -> None:
        """Persist the last suggestion for the user to rotate on subsequent calls."""
        if not user_key:
            return
        _SUGGESTION_MEMORY.setdefault(user_key, []).append((slot_iso, now))

    async def list_next_slots(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Devuelve las N horas disponibles más cercanas (mañana/tarde) empezando desde 'desde' o desde hoy.
        Args:
        - block: "morning"/"manana"/"mañana" o "afternoon"/"tarde" (opcional, default todo el día)
        - desde: fecha inicial (YYYY-MM-DD) para comenzar la búsqueda (opcional, default hoy; si es pasada/ inválida => hoy + 7)
        """
        block = (arguments.get("block") or "").lower()
        duration_minutes = DEFAULT_DURATION_MINUTES
        top_n = DEFAULT_TOP_N
        tz_str = DEFAULT_TIMEZONE
        desde_str = arguments.get("desde")

        try:
            tzinfo = ZoneInfo(tz_str)
        except Exception:
            tzinfo = timezone.utc
            tz_str = "UTC"

        now = datetime.now(tzinfo)

        if desde_str:
            try:
                search_date = datetime.fromisoformat(desde_str).date()
            except Exception:
                search_date = now.date() + timedelta(days=7)
        else:
            search_date = now.date()

        # If the requested start date is today or in the past, ignore it and use today.
        if search_date <= now.date():
            search_date = now.date()
            desde_str = None

        normalized_desde = search_date.isoformat() if desde_str else None

        user_key = str(arguments.get("chat_id") or arguments.get("customer_number") or "").strip()
        memory_now = datetime.now(tzinfo)
        recent_suggestions = set(self._recent_suggestions(user_key, memory_now)) if user_key else set()

        slots: List[Dict[str, Any]] = []
        day_offset = 0

        while len(slots) < top_n and day_offset < MAX_SLOT_SEARCH_DAYS:
            current_date = search_date + timedelta(days=day_offset)
            day_offset += 1

            intervals = _intervals_for_date(datetime.combine(current_date, datetime.min.time(), tzinfo), block)
            if not intervals:
                continue

            for start_dt, end_dt in intervals:
                body = {
                    "timeMin": start_dt.isoformat(),
                    "timeMax": end_dt.isoformat(),
                    "items": [{"id": self.calendar_id}],
                    "timeZone": tz_str,
                }
                response = self.service.freebusy().query(body=body).execute()
                busy_slots = response.get("calendars", {}).get(self.calendar_id, {}).get("busy", [])

                cursor = start_dt
                if current_date == now.date() and cursor < now:
                    cursor = now
                cursor = _align_to_slot(cursor, duration_minutes)

                while cursor + timedelta(minutes=duration_minutes) <= end_dt and len(slots) < top_n:
                    candidate_start = cursor
                    candidate_end = cursor + timedelta(minutes=duration_minutes)

                    overlap = False
                    for busy in busy_slots:
                        busy_start = datetime.fromisoformat(busy["start"])
                        busy_end = datetime.fromisoformat(busy["end"])
                        if candidate_start < busy_end and candidate_end > busy_start:
                            overlap = True
                            break

                    if user_key and candidate_start.isoformat() in recent_suggestions:
                        overlap = True  # treat recently suggested slots as unavailable for this user

                    if not overlap:
                        slots.append(
                            {
                                "slot_start_iso": candidate_start.isoformat(),
                                "slot_end_iso": candidate_end.isoformat(),
                                "slot_speech": _slot_speech(candidate_start, tz_str),
                            }
                        )

                    cursor = candidate_end

        slots_sorted = sorted(slots, key=lambda s: s["slot_start_iso"])
        top_slots = slots_sorted[:top_n]

        candidate = None
        for slot in top_slots:
            if slot["slot_start_iso"] in recent_suggestions:
                continue  # treat already suggested slots as unavailable for this user
            if slot["slot_start_iso"] not in recent_suggestions:
                candidate = slot
                break

        if candidate is None and top_slots and user_key:
            # Rotate again if all options have been offered in the last TTL window.
            _SUGGESTION_MEMORY[user_key] = []
            candidate = top_slots[0]

        suggested = candidate or (top_slots[0] if top_slots else None)
        if suggested and user_key:
            self._remember_suggestion(user_key, suggested["slot_start_iso"], memory_now)

        return {
            "available_slots": top_slots,
            "suggested": suggested,
            "block": block or "all_day",
            "desde": normalized_desde,
        }
