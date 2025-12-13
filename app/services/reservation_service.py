import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.payload_cleaner import CleanPayload

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]


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
            if tool == "convert_date":
                return await self.convert_date(args)
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

        # Replace literal \n from .env with real newlines
        private_key = os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")

        info = {
            "type": "service_account",
            "project_id": os.getenv("GOOGLE_PROJECT_ID"),
            "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"),
            "private_key": private_key,
            "client_email": os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL"),
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),  # optional
            "token_uri": "https://oauth2.googleapis.com/token",
        }

        credentials = Credentials.from_service_account_info(
            info, scopes=CALENDAR_SCOPES
        )
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def _extract_time_window(self, arguments: Dict[str, Any]) -> Tuple[str, str, str]:
        date_str = arguments.get("date")
        time_str = arguments.get("time")
        duration_minutes = 60 # default duration
        tz_str = arguments.get("timezone", "Europe/Madrid")

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

    async def check_availability(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        start_iso, end_iso, tz_str = self._extract_time_window(arguments)

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
        # Check availability first to avoid double booking
        availability = await self.check_availability(arguments)
        if not availability.get("available", False):
            return {"created": False, "reason": "not_available", "busy": availability.get("busy")}

        start_iso, end_iso, tz_str = self._extract_time_window(arguments)
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

        created = (
            self.service.events()
            .insert(calendarId=self.calendar_id, body=event_body)
            .execute()
        )
        return {"created": True, "event": created}

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
            start_iso, end_iso, tz_str = self._extract_time_window(arguments)
            updates["start"] = {"dateTime": start_iso, "timeZone": tz_str}
            updates["end"] = {"dateTime": end_iso, "timeZone": tz_str}
        if "name" in arguments:
            updates["summary"] = f"Cita agendada con {arguments['name']}"
            # Only rewrite description if we also have date/time to avoid losing data
            if "date" in arguments and "time" in arguments:
                customer_number = arguments.get("customer_number", "No proporcionado")
                reforma = arguments.get("reforma", "No especificada")
                updates["description"] = (
                    f"Telefono: {customer_number}\n"
                    f"Nombre: {arguments['name']}\n"
                    f"Fecha: {arguments.get('date')}\n"
                    f"Hora: {arguments.get('time')}\n"
                    f"Duracion: {arguments.get('duration_minutes', 60)} minutos\n"
                    f"Zona horaria: {arguments.get('timezone', 'Europe/Madrid')}\n"
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
        Lista slots disponibles para un día dado dentro de una ventana horaria.
        Args esperados:
        - date (YYYY-MM-DD) obligatorio
        - start_time (HH:MM) opcional, por defecto 09:00
        - end_time (HH:MM) opcional, por defecto 18:00
        - duration_minutes opcional, por defecto 60
        - timezone opcional, por defecto Europe/Madrid
        """
        date_str = arguments.get("date")
        if not date_str:
            raise ValueError("Missing 'date' to list available slots.")

        start_time_str = arguments.get("start_time", "09:00")
        end_time_str = arguments.get("end_time", "18:00")
        duration_minutes = int(arguments.get("duration_minutes", 60))
        tz_str = arguments.get("timezone", "Europe/Madrid")

        try:
            tzinfo = ZoneInfo(tz_str)
        except Exception:
            tzinfo = timezone.utc
            tz_str = "UTC"

        day_start = datetime.fromisoformat(f"{date_str}T{start_time_str}")
        day_end = datetime.fromisoformat(f"{date_str}T{end_time_str}")
        day_start = day_start.replace(tzinfo=tzinfo)
        day_end = day_end.replace(tzinfo=tzinfo)

        # Obtener slots ocupados
        body = {
            "timeMin": day_start.isoformat(),
            "timeMax": day_end.isoformat(),
            "items": [{"id": self.calendar_id}],
            "timeZone": tz_str,
        }
        response = self.service.freebusy().query(body=body).execute()
        busy_slots = response.get("calendars", {}).get(self.calendar_id, {}).get(
            "busy", []
        )

        # Construir lista de slots libres
        slots = []
        cursor = day_start
        while cursor + timedelta(minutes=duration_minutes) <= day_end:
            candidate_start = cursor
            candidate_end = cursor + timedelta(minutes=duration_minutes)

            overlap = False
            for busy in busy_slots:
                busy_start = datetime.fromisoformat(busy["start"])
                busy_end = datetime.fromisoformat(busy["end"])
                # detectar solapamiento
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

        return {
            "available_slots": slots,
            "busy": busy_slots,
            "window": {
                "start": day_start.isoformat(),
                "end": day_end.isoformat(),
                "timeZone": tz_str,
            },
            "duration_minutes": duration_minutes,
        }

    async def convert_date(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convierte frases relativas a fechas/horas en valores concretos basados en la fecha actual (server).
        Espera:
        - date_text (str): e.g. "mañana", "pasado mañana", "dentro de una semana", "proximo lunes"
        - time_text (opcional): e.g. "por la tarde", "por la mañana", "mediodia", "por la noche"
        Retorna: today_date, proposed_date (YYYY-MM-DD), proposed_time (HH:MM), timezone.
        """
        date_text = (arguments.get("date_text") or arguments.get("date") or "").lower().strip()
        time_text = (arguments.get("time_text") or arguments.get("time") or "").lower().strip()
        tz_str = arguments.get("timezone", "Europe/Madrid")

        try:
            tzinfo = ZoneInfo(tz_str)
        except Exception:
            tzinfo = timezone.utc
            tz_str = "UTC"

        now = datetime.now(tzinfo)
        today_date = now.date()

        def next_weekday(target_weekday: int) -> datetime.date:
            days_ahead = (target_weekday - today_date.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today_date + timedelta(days=days_ahead)

        def parse_date_phrase(text: str) -> datetime.date:
            if not text:
                raise ValueError("Missing 'date_text' to convert date.")
            if "hoy" in text:
                assumptions.append("Interpretado 'hoy' como fecha actual.")
                return today_date
            if "mañana" in text and "pasado" not in text:
                assumptions.append("Interpretado 'mañana' como +1 día.")
                return today_date + timedelta(days=1)
            if "pasado mañana" in text:
                assumptions.append("Interpretado 'pasado mañana' como +2 días.")
                return today_date + timedelta(days=2)
            if "fin de semana" in text:
                assumptions.append("Interpretado 'próximo fin de semana' como sábado siguiente.")
                return next_weekday(5)  # Saturday
            if "semana" in text:
                assumptions.append("Interpretado 'en una semana' como +7 días.")
                return today_date + timedelta(days=7)
            days_map = {
                "lunes": 0,
                "martes": 1,
                "miercoles": 2,
                "miércoles": 2,
                "jueves": 3,
                "viernes": 4,
                "sabado": 5,
                "sábado": 5,
                "domingo": 6,
            }
            for key, idx in days_map.items():
                if key in text:
                    assumptions.append(f"Interpretado '{key}' como próximo {key}.")
                    return next_weekday(idx)
            # fallback: try YYYY-MM-DD
            try:
                return datetime.fromisoformat(text).date()
            except Exception:
                raise ValueError(f"Cannot parse date_text '{text}'")

        def parse_time_phrase(text: str) -> str:
            if not text:
                assumptions.append("Sin hora explícita; usando 17:00 (tarde).")
                return "17:00"  # default afternoon
            mapping = {
                "mañana": "10:00",
                "manana": "10:00",
                "mediodia": "12:00",
                "mediodía": "12:00",
                "tarde": "17:00",
                "esta tarde": "17:00",
                "noche": "20:00",
            }
            for key, val in mapping.items():
                if key in text:
                    if "hora explícita" not in assumptions:
                        assumptions.append(f"Interpretado '{key}' como {val}.")
                    return val
            # if text looks like HH:MM keep it
            if ":" in text:
                return text
            assumptions.append("No se reconoció hora; usando 17:00.")
            return "17:00"

        assumptions: list[str] = []
        proposed_date = parse_date_phrase(date_text)
        proposed_time = parse_time_phrase(time_text)
        confidence = "assumed" if assumptions else "exact"

        return {
            "today_date": today_date.isoformat(),
            "proposed_date": proposed_date.isoformat(),
            "proposed_time": proposed_time,
            "timezone": tz_str,
            "phrase": {"date_text": date_text, "time_text": time_text},
            "assumptions": assumptions,
            "confidence": confidence,
        }
