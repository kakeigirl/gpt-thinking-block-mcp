FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir 'psycopg[binary]>=3.2,<4'

COPY server.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=3s --start-period=3s --retries=3 \
  CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=2)"]

CMD ["python3", "server.py", "8787"]
