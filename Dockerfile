FROM python:3.12-slim

# Systempakete für Ansible + SSH
RUN apt-get update && apt-get install -y \
    ansible \
    openssh-client \
    git \
    && rm -rf /var/lib/apt/lists/*

# Nicht-root Benutzer erstellen
RUN useradd -m deployuser

# Python-Abhängigkeiten
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App-Code
COPY api.py ./
COPY config.yaml ./

# Besitzer setzen und auf Nicht-Root wechseln
RUN chown -R deployuser:deployuser /app
USER deployuser

# Expose the port the app runs on
EXPOSE 8000

# Start FastAPI mit Uvicorn
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
