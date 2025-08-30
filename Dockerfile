# Use official Python image
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt /app/
RUN apt-get update && apt-get install -y build-essential libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-0 libffi-dev && \
    pip install --no-cache-dir -r requirements.txt

COPY . /app
EXPOSE 5000
ENV FLASK_APP=app.py
CMD ["python", "app.py"]
