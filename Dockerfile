FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs

# Hugging Face Spaces uses 7860 by default; other platforms supply their own PORT.
EXPOSE 7860

CMD ["python", "-m", "waifu"]
