FROM python:3.12-slim

WORKDIR /app

# Сначала зависимости — слой кешируется между сборками.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Затем код бота.
COPY . .

# Бот работает на long polling (без веб-сервера и webhook).
CMD ["python", "bot.py"]
