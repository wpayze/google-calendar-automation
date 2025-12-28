import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # carga .env antes de importar el bot para que las env vars estén disponibles

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.whatsapp import handle_whatsapp_message

PHONE = "console-user"

print("Bot listo. Escribe mensajes (Ctrl+C para salir)\n")


async def main() -> None:
    while True:
        user_input = input("Tú: ")
        resp = await handle_whatsapp_message(PHONE, user_input)

        # Extraer texto del TwiML
        xml = resp.body.decode()
        start = xml.find("<Body>") + 6
        end = xml.find("</Body>")
        bot_msg = xml[start:end]

        print(f"Bot: {bot_msg}\n")


if __name__ == "__main__":
    asyncio.run(main())
