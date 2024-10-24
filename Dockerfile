# Use an official Python runtime as a parent image
FROM python:3.9-slim as builder

# Set environment variables to avoid installing __pycache__ and other files
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container to /app
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc

# Install any needed packages specified in requirements.txt into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements.txt first to leverage Docker cache
COPY requirements.txt .

# Install dependencies
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Second stage: Create the final runtime image
FROM python:3.9-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PATH="/opt/venv/bin:$PATH"

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Set the working directory in the container to /app
WORKDIR /app

# Copy the application code
COPY . /app

# Command to run the uvicorn server
CMD ["python", "-m", "backend.fastapi.main", "--mode", "prod", "--host", "0.0.0.0"]