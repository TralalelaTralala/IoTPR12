FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py mapping.json ./
EXPOSE 4840
CMD ["python", "-u", "opcua_server.py"]
