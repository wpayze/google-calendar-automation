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
        duration_minutes = int(arguments.get("duration_minutes", 60))
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
        start_iso, end_iso, tz_str = self._extract_time_window(arguments)
        name = arguments.get("name", "Invitado")
        summary = f"Cita agendada con {name}"
        description = (
            f"Nombre: {name}\n"
            f"Fecha: {arguments.get('date')}\n"
            f"Hora: {arguments.get('time')}\n"
            f"Duracion: {arguments.get('duration_minutes', 60)} minutos\n"
            f"Zona horaria: {tz_str}"
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
                updates["description"] = (
                    f"Nombre: {arguments['name']}\n"
                    f"Fecha: {arguments.get('date')}\n"
                    f"Hora: {arguments.get('time')}\n"
                    f"Duracion: {arguments.get('duration_minutes', 60)} minutos\n"
                    f"Zona horaria: {arguments.get('timezone', 'Europe/Madrid')}"
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
