FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY rag/requirements.txt ./rag/requirements.txt
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r ./rag/requirements.txt

COPY rag ./rag
RUN pip install --no-cache-dir ./rag --no-deps

COPY agent ./agent
RUN pip install --no-cache-dir ./agent

EXPOSE 8002

CMD ["uvicorn", "agent_app.app.main:app", "--host", "0.0.0.0", "--port", "8002"]
