FROM python:3.11-slim

WORKDIR /app

# Asennetaan järjestelmäriippuvuudet Voikkoa varten
RUN apt-get update && apt-get install -y \
    libvoikko1 \
    voikko-fi \
    && rm -rf /var/lib/apt/lists/*

# Asennetaan riippuvuudet
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopioidaan lähdekoodi
COPY . .

EXPOSE 8080
