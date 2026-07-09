FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
COPY agent_app ./agent_app

RUN pip install --no-cache-dir -r requirements.txt

ENV HOST=0.0.0.0
ENV PORT=8787

EXPOSE 8787

CMD ["python", "agent_app/app.py"]
