FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

# Apply migrations once, then preload the app factory so init_db() and the
# superadmin seed run a single time in the master process (workers fork and
# never race on DDL against PostgreSQL).
CMD ["sh", "-c", "python migrate.py && exec gunicorn --bind 0.0.0.0:5000 --workers 3 --preload --access-logfile - 'app:create_app()'"]