# Use an official Python runtime as the base image
FROM python:3.11-slim

WORKDIR /usr/src/app

RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

ENV LOCAL=false

COPY main.py .
COPY modules .

RUN mkdir -p /plates /config

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python"]
CMD ["./main.py"]