FROM python:3.12-slim

# Set a working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    curl \
 && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip setuptools
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy project files
COPY . /app

# Expose Streamlit port
EXPOSE 8501

# Default env: ensure database path is writable in container
ENV SOMALIA_EIP_DB_PATH=/tmp/somalia_eip.db

# Streamlit runs on container start
CMD ["streamlit", "run", "dashboard/app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
