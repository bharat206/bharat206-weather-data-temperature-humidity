Q- Problem Statement You are tasked with building a small backend service that fetches time-series weather data (temperature & humidity) from the Open-Meteo API and generates both an Excel file and a PDF report with a chart.
# Flask Weather Service

A backend service that fetches hourly temperature & humidity from Open-Meteo for the past 2 days and provides:

- `GET /weather-report?lat={lat}&lon={lon}`: Fetches from Open-Meteo and stores to SQLite.
- `GET /export/excel`: Downloads last 48 hours as weather_last_48h.xlsx.
- `GET /export/pdf`: Downloads a PDF report (title, metadata, chart).

## Run locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Run with Docker

```bash
docker build -t flask-weather .
docker run -p 5000:5000 flask-weather
```

or

```bash
docker-compose up --build
```

## Notes

- Example outputs (`weather_data.xlsx` and `weather_report.pdf`) are included in this repo for reference.
- WeasyPrint requires system packages on some OSes (Cairo, Pango). If unavailable, the app falls back to generating a PDF using Matplotlib.
- Timestamps are handled in UTC.
