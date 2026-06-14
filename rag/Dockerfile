FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

EXPOSE 8001

CMD ["uvicorn", "rag_app.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
