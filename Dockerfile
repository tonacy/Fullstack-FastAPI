# Use a minimal Python runtime as a parent image
FROM python:3.9-alpine as builder

# Set environment variables to optimize for memory usage
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONOPTIMIZE=1

# Set the working directory in the container to /app
WORKDIR /app

# Install build dependencies (if needed)
RUN apk add --no-cache gcc musl-dev libffi-dev

# Add current directory code to /app in the container
ADD . /app

# Install any needed packages specified in requirements.txt
RUN pip install --upgrade pip && \
    pip install --user -r requirements.txt && \
    rm -rf /root/.cache/pip

# This is the second stage where we create the runtime image
FROM python:3.9-alpine

# Set environment variables to optimize for memory usage
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONOPTIMIZE=1
ENV PATH=/root/.local/bin:$PATH

# Copy the dependencies from the build stage
COPY --from=builder /root/.local /root/.local

# Set the working directory in the container to /app
WORKDIR /app

# Add current directory code to /app in the container
ADD . /app

# Command to run the uvicorn server
CMD ["python", "-m", "backend.app.main", "--mode", "prod", "--host", "0.0.0.0"]
