from whatsapp import handle_whatsapp_message  # ajusta el import

PHONE = "console-user"

print("Bot listo. Escribe mensajes (Ctrl+C para salir)\n")

while True:
    user_input = input("Tú: ")
    resp = handle_whatsapp_message(PHONE, user_input)

    # Extraer texto del TwiML
    xml = resp.body.decode()
    start = xml.find("<Body>") + 6
    end = xml.find("</Body>")
    bot_msg = xml[start:end]

    print(f"Bot: {bot_msg}\n")
