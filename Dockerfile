# PrOxy Trading Terminal - Fly.io image
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# xgboost needs libgomp; streamlit/pyarrow pull libstdc++ deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["bash", "start.sh"]
