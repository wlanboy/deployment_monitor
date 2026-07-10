### Build-Stage: Python-Abhängigkeiten in ein venv installieren
FROM python:3.14-slim AS builder

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

### Runtime-Stage: schlankes Image ohne Build-Tools
FROM python:3.14-slim

# Systempakete für Ansible + SSH (zur Laufzeit benötigt)
RUN apt-get update && apt-get install -y \
    ansible \
    openssh-client \
    git \
    && rm -rf /var/lib/apt/lists/*

# Nicht-root Benutzer erstellen
RUN useradd -m deployuser

# Fertiges venv aus der Build-Stage übernehmen
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# App-Code
WORKDIR /app
COPY *.py ./
COPY config.yaml ./

# Besitzer setzen und auf Nicht-Root wechseln
RUN chown -R deployuser:deployuser /app
USER deployuser

# Expose the port the app runs on
EXPOSE 8000

# Start FastAPI mit Uvicorn
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
