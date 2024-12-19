# Use an official Python runtime as the base image
FROM python:3.11-slim

# Set working directory
WORKDIR /usr/src/app

# Reduce layer size: combine apt-get update and clean-up in one RUN
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file separately for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
# Set environment variable
ENV LOCAL=false
ENV TZ=Africa/Johannesburg
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/usr/src/app"

# Create necessary directories
RUN mkdir -p /plates /config

# Copy application files
COPY modules/ ./modules/
COPY main.py .

# Define entry point and default command
ENTRYPOINT ["python"]
CMD ["main.py"]
