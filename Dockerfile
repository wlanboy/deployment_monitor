### Build-Stage: Python-Abhängigkeiten (inkl. ansible-core) in ein venv installieren
FROM python:3.14-slim AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .
# Nur fertige Wheels: kein Kompilieren aus Quellpaketen, keine Build-Tools nötig
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --only-binary=:all: -r requirements.txt

### Runtime-Stage: schlankes Image ohne Build-Tools
FROM python:3.14-slim

# Systempakete, die Ansible zur Laufzeit braucht (Ansible selbst kommt aus dem venv)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    git \
    && rm -rf /var/lib/apt/lists/*

# Nicht-root Benutzer erstellen
RUN useradd -m deployuser

# Fertiges venv aus der Build-Stage übernehmen
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ANSIBLE_NOCOLOR=1

# App-Code
WORKDIR /app
COPY *.py ./
COPY config.yaml ./

# Datenverzeichnis im Image anlegen, damit das Named Volume deployuser gehört.
# Nur dieses Verzeichnis – der App-Code bleibt root-eigen und für deployuser schreibgeschützt.
RUN mkdir -p /app/deploymentjobs && chown deployuser:deployuser /app/deploymentjobs
USER deployuser

# Expose the port the app runs on
EXPOSE 8000

# Start FastAPI mit Uvicorn
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
