FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py index.html ./
COPY static ./static
ENV VB_DATA_DIR=/data VB_SECURE=1 PORT=8080 PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
CMD ["python3", "server.py"]
