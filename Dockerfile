FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . && useradd --create-home worker && mkdir /app/data && chown worker:worker /app/data
USER worker
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
