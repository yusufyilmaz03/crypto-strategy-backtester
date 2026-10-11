# Runs the supervisor (dashboard + enabled bot instances). See README, "Running 24/7".
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kripto_bot ./kripto_bot

# The panel listens on all interfaces inside the container; it refuses to start
# without DASHBOARD_PASSWORD. docker-compose.yml publishes it on the host's 127.0.0.1 only.
ENV PYTHONUNBUFFERED=1 DASHBOARD_HOST=0.0.0.0
EXPOSE 5002
VOLUME ["/app/runtime", "/app/results", "/app/data"]
CMD ["python", "-m", "kripto_bot", "run"]
