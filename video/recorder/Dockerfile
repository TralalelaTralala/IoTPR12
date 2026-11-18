FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
RUN pip install --no-cache-dir playwright==1.47.0 asyncua==1.1.5 cryptography==42.0.8
WORKDIR /app
COPY *.py ./
CMD ["python3", "-u", "record.py"]
