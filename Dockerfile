FROM python:3.10-slim

WORKDIR /app

# megatools aur dusre tools install karna (ffmpeg add kiya gaya hai safety ke liye)
RUN apt-get update && apt-get install -y \
    megatools \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "app.py"]
