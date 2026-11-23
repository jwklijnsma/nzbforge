# ---- frontend ----
FROM node:26-alpine AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- backend ----
FROM python:3.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/nzbforge.db
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=ui /src/frontend/dist frontend/dist
RUN useradd -r -u 1000 nzbforge && mkdir /data && chown nzbforge /data
USER nzbforge
VOLUME /data
WORKDIR /app/backend
EXPOSE 8000
# API + UI + header scanner (background thread, see SCANNER in .env.example)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
