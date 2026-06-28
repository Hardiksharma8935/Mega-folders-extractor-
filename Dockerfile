# Base Python image
FROM python:3.10-slim

# Install system dependencies (FFMPEG for video splitting)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your app's code
COPY . .

# Run the bot
CMD ["python", "main.py"]
