# Use an official Python runtime as the base image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Python script into the container
COPY index.py .

# Create additional directories inside the container
RUN mkdir -p /plates /config

# Set the container entry point and command
ENTRYPOINT ["python"]
CMD ["./index.py"]