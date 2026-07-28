FROM python:3.11-slim

WORKDIR /app

# Asennetaan riippuvuudet
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopioidaan lähdekoodi
COPY . .

EXPOSE 8080
