FROM python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml backend/requirements.lock ./
COPY backend/app ./app
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-deps .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
