# Build context is the repo root (see docker-compose.yml) so this can
# copy in the sibling `agent/` package as a local editable dependency --
# see requirements.txt's `-e ../agent` line.
FROM python:3.12-slim

WORKDIR /app

COPY agent /app/agent
RUN pip install --no-cache-dir /app/agent

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
WORKDIR /app/backend

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
