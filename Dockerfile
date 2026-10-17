# Small official Python image. Pin the minor version so builds are repeatable.
FROM python:3.11-slim

# Don't write .pyc files, and don't buffer logs (so you see output immediately).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy ONLY the dependency list first. Docker caches each step ("layer"),
# so as long as requirements.txt is unchanged, the slow pip install below
# is reused even when your code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the application code.
COPY . .

# Run as a normal user, not root, so a bug or exploit has less power.
RUN useradd --create-home appuser && mkdir -p /data && chown -R appuser /app /data
USER appuser

# Where the bot keeps its "already seen" state (mounted as a volume in compose).
ENV SEEN_FILE=/data/seen_jobs.json

CMD ["python", "main.py"]
