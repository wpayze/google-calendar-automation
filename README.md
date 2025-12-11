## Entorno virtual
```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

## Instalación
```bash
pip install -r requirements.txt
```

## Ejecutar en local
```bash
uvicorn app.main:app --reload --port 8000
```

## Docker (opcional)
```bash
docker build -t reservation-webhook .
docker run --env-file .env -p 8000:8000 reservation-webhook
```
