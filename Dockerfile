FROM python:3.10-slim

# Railway me memory limits ke liye environment optimization
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y \
    curl \
    libc6 \
    libglib2.0-0 \
    libicu-dev \
    libssl-dev \
    procps \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# MEGA-CMD installation 
RUN curl -o megacmd.deb https://mega.nz/linux/repo/Debian_11/amd64/megacmd-Debian_11_amd64.deb \
    && apt-get update \
    && apt-get install -y ./megacmd.deb \
    && rm megacmd.deb \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt 

COPY . .

CMD ["python", "app.py"]
