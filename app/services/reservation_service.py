import os
from datetime import datetime, timedelta, timezone, time
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.payload_cleaner import CleanPayload

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
DEFAULT_TOP_N = 5
DEFAULT_DURATION_MINUTES = 60
DEFAULT_TIMEZONE = "Europe/Madrid"


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
            if tool == "update_reservation":
                return await self.update_reservation(args)
            if tool == "delete_reservation":
                return await self.delete_reservation(args)
            if tool == "list_available_slots":
                return await self.list_available_slots(args)
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
        duration_minutes = 60  # default duration
        tz_str = arguments.get("timezone", DEFAULT_TIMEZONE)

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

    def _ensure_business_hours(self, date_str: str, time_str: str, tz_str: str) -> None:
        if not date_str or not time_str:
            return
        try:
            tzinfo = ZoneInfo(tz_str)
        except Exception:
            tzinfo = timezone.utc
        dt = datetime.fromisoformat(f"{date_str}T{time_str}").replace(tzinfo=tzinfo)
        weekday = dt.weekday()  # 0 = Monday, 6 = Sunday
        if weekday >= 5:
            raise ValueError("Outside business hours (Mon-Fri 09:00-14:00 and 16:00-19:00).")
        t = dt.time()
        morning_start = time(9, 0)
        morning_end = time(14, 0)
        afternoon_start = time(16, 0)
        afternoon_end = time(19, 0)
        if not ((morning_start <= t < morning_end) or (afternoon_start <= t < afternoon_end)):
            raise ValueError("Outside business hours (Mon-Fri 09:00-14:00 and 16:00-19:00).")

    async def check_availability(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Consulta disponibilidad para un slot específico."""
        self._ensure_business_hours(
            arguments.get("date"),
            arguments.get("time"),
            DEFAULT_TIMEZONE,
        )
        args_with_tz = {**arguments, "timezone": DEFAULT_TIMEZONE}
        start_iso, end_iso, tz_str = self._extract_time_window(args_with_tz)

        body = {
            "timeMin": start_iso,
            "timeMax": end_iso,
            "items": [{"id": self.calendar_id}],
            "timeZone": tz_str,
        }
        response = self.service.freebusy().query(body=body).execute()
        busy_slots = response.get("calendars", {}).get(self.calendar_id, {}).get(
            "busy", []
        )

        return {
            "available": len(busy_slots) == 0,
            "busy": busy_slots,
            "window": {"start": start_iso, "end": end_iso, "timeZone": tz_str},
        }

    async def create_reservation(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Crea una reserva y confirma si está dentro de horario disponible y laboral."""
        self._ensure_business_hours(
            arguments.get("date"),
            arguments.get("time"),
            DEFAULT_TIMEZONE,
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

    async def update_reservation(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        event_id = arguments.get("event_id")
        if not event_id:
            raise ValueError("Missing 'event_id' to update the reservation.")

        updates: Dict[str, Any] = {}
        if "summary" in arguments:
            updates["summary"] = arguments["summary"]
        if "description" in arguments:
            updates["description"] = arguments["description"]
        if "date" in arguments and "time" in arguments:
            self._ensure_business_hours(
                arguments.get("date"),
                arguments.get("time"),
                DEFAULT_TIMEZONE,
            )
            args_with_tz = {**arguments, "timezone": DEFAULT_TIMEZONE}
            start_iso, end_iso, tz_str = self._extract_time_window(args_with_tz)
            updates["start"] = {"dateTime": start_iso, "timeZone": tz_str}
            updates["end"] = {"dateTime": end_iso, "timeZone": tz_str}
        if "name" in arguments:
            updates["summary"] = f"Cita agendada con {arguments['name']}"
            if "date" in arguments and "time" in arguments:
                customer_number = arguments.get("customer_number", "No proporcionado")
                reforma = arguments.get("reforma", "No especificada")
                updates["description"] = (
                    f"Telefono: {customer_number}\n"
                    f"Nombre: {arguments['name']}\n"
                    f"Fecha: {arguments.get('date')}\n"
                    f"Hora: {arguments.get('time')}\n"
                    f"Duracion: {arguments.get('duration_minutes', 60)} minutos\n"
                    f"Zona horaria: {arguments.get('timezone', DEFAULT_TIMEZONE)}\n"
                    f"Al usuario le interesa {reforma}"
                )

        if not updates:
            raise ValueError("No fields provided to update.")

        updated = (
            self.service.events()
            .patch(calendarId=self.calendar_id, eventId=event_id, body=updates)
            .execute()
        )
        return {"updated": True, "event": updated}

    async def delete_reservation(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        event_id = arguments.get("event_id")
        if not event_id:
            raise ValueError("Missing 'event_id' to delete the reservation.")

        self.service.events().delete(
            calendarId=self.calendar_id, eventId=event_id
        ).execute()
        return {"deleted": True, "event_id": event_id}

    async def list_available_slots(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Devuelve las N horas disponibles más cercanas en un bloque (mañana/tarde) para un día, a partir de 'desde'.
        Args:
        - date (YYYY-MM-DD) obligatorio
        - block: "morning"/"manana"/"mañana" o "afternoon"/"tarde" (opcional, default todo el día)
        - desde: fecha inicial para buscar (default hoy)
        """
        date_str = arguments.get("date")
        if not date_str:
            raise ValueError("Missing 'date' to list available slots.")

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

        if block in {"morning", "manana", "mañana"}:
            start_time_str, end_time_str = "09:00", "14:00"
        elif block in {"afternoon", "tarde"}:
            start_time_str, end_time_str = "16:00", "19:00"
        else:
            start_time_str, end_time_str = "09:00", "19:00"

        day_start = datetime.fromisoformat(f"{date_str}T{start_time_str}").replace(tzinfo=tzinfo)
        day_end = datetime.fromisoformat(f"{date_str}T{end_time_str}").replace(tzinfo=tzinfo)

        if desde_str:
            try:
                search_start = datetime.fromisoformat(desde_str).date()
            except Exception:
                raise ValueError("Invalid 'desde'; expected YYYY-MM-DD.")
            if day_start.date() < search_start:
                day_start = datetime.combine(search_start, day_start.time(), tzinfo)
                day_end = datetime.combine(search_start, day_end.time(), tzinfo)

        body = {
            "timeMin": day_start.isoformat(),
            "timeMax": day_end.isoformat(),
            "items": [{"id": self.calendar_id}],
            "timeZone": tz_str,
        }
        response = self.service.freebusy().query(body=body).execute()
        busy_slots = response.get("calendars", {}).get(self.calendar_id, {}).get("busy", [])

        slots = []
        cursor = day_start
        while cursor + timedelta(minutes=duration_minutes) <= day_end:
            candidate_start = cursor
            candidate_end = cursor + timedelta(minutes=duration_minutes)

            overlap = False
            for busy in busy_slots:
                busy_start = datetime.fromisoformat(busy["start"])
                busy_end = datetime.fromisoformat(busy["end"])
                if candidate_start < busy_end and candidate_end > busy_start:
                    overlap = True
                    break

            if not overlap:
                slots.append(
                    {
                        "start": candidate_start.isoformat(),
                        "end": candidate_end.isoformat(),
                        "timeZone": tz_str,
                    }
                )

            cursor = candidate_end

        slots_sorted = sorted(slots, key=lambda s: s["start"])
        top_slots = slots_sorted[:top_n]
        suggested = top_slots[0] if top_slots else None

        return {
            "available_slots": top_slots,
            "suggested": suggested,
        }
