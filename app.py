#!/usr/bin/env python3
import os
import io
import base64
import sqlite3
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import matplotlib.pyplot as plt
from flask import Flask, request, jsonify, send_file
from weasyprint import HTML

# Path to SQLite database
DB_PATH = os.environ.get(
    "WEATHER_DB_PATH", os.path.join(os.path.dirname(__file__), "weather.sqlite3")
)

app = Flask(__name__)


# --- Database Helpers ---
def get_conn():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weather (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                temperature_2m REAL,
                relative_humidity_2m REAL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                UNIQUE (timestamp, latitude, longitude)
            )
            """
        )
        conn.commit()


init_db()


# --- Open-Meteo Fetch ---
def fetch_open_meteo(lat: float, lon: float) -> pd.DataFrame:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m",
        "past_days": 2,
        "timezone": "UTC",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    hums = hourly.get("relative_humidity_2m", [])

    if not (times and temps and hums):
        raise ValueError("Open-Meteo response missing expected hourly data.")

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(times, utc=True),
            "temperature_2m": temps,
            "relative_humidity_2m": hums,
        }
    ).sort_values("timestamp")

    df["latitude"] = float(lat)
    df["longitude"] = float(lon)
    return df


# --- DB Insert/Query ---
def upsert_records(df: pd.DataFrame) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        rows = 0
        for _, row in df.iterrows():
            cur.execute(
                """
                INSERT OR REPLACE INTO weather
                (timestamp, temperature_2m, relative_humidity_2m, latitude, longitude)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    float(row["temperature_2m"]),
                    float(row["relative_humidity_2m"]),
                    float(row["latitude"]),
                    float(row["longitude"]),
                ),
            )
            rows += 1
        conn.commit()
        return rows


def query_last_48h() -> pd.DataFrame:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with get_conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT timestamp, temperature_2m, relative_humidity_2m, latitude, longitude
            FROM weather
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            """,
            conn,
            params=(cutoff,),
            parse_dates=["timestamp"],
        )
    return df


# --- Flask Routes ---
@app.get("/weather-report")
def weather_report():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "Please provide numeric lat & lon query params"}), 400

    try:
        df = fetch_open_meteo(lat, lon)
        upserted = upsert_records(df)
        return jsonify(
            {
                "message": "Data fetched and stored",
                "rows_upserted": upserted,
                "from": df["timestamp"].min().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": df["timestamp"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "samples": int(df.shape[0]),
            }
        )
    except requests.HTTPError as e:
        return jsonify({"error": f"Open-Meteo HTTP error: {e}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/export/excel")
def export_excel():
    df = query_last_48h()
    if df.empty:
        return (
            jsonify({"error": "No data available. Call /weather-report first."}),
            400,
        )

    out = df[["timestamp", "temperature_2m", "relative_humidity_2m"]].copy()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        out.to_excel(writer, sheet_name="last_48_hours", index=False)
    buf.seek(0)

    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="weather_last_48h.xlsx",
    )


@app.get("/export/pdf")
def export_pdf():
    df = query_last_48h()
    if df.empty:
        return (
            jsonify({"error": "No data available. Call /weather-report first."}),
            400,
        )

    # --- Chart ---
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(df["timestamp"], df["temperature_2m"], label="Temperature (°C)")
    ax.plot(df["timestamp"], df["relative_humidity_2m"], label="Humidity (%)")
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Value")
    ax.set_title("Temperature & Humidity - Last 48 Hours")
    ax.legend(loc="best")
    ax.grid(True, linestyle="--", linewidth=0.5)

    img_buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(img_buf, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    img_buf.seek(0)
    img_b64 = base64.b64encode(img_buf.read()).decode("ascii")

    start_ts = df["timestamp"].min().strftime("%Y-%m-%d %H:%M UTC")
    end_ts = df["timestamp"].max().strftime("%Y-%m-%d %H:%M UTC")
    lat = df["latitude"].mode().iloc[0] if "latitude" in df else None
    lon = df["longitude"].mode().iloc[0] if "longitude" in df else None
    location_text = (
        f"Lat: {lat:.4f}, Lon: {lon:.4f}" if lat is not None and lon is not None else "Location: N/A"
    )

    # --- HTML template ---
    html = f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: Arial, Helvetica, sans-serif; margin: 30px; }}
        h1 {{ margin-bottom: 0; }}
        .meta {{ color: #333; margin-bottom: 20px; }}
        .footer {{ font-size: 12px; color: #666; margin-top: 30px; }}
        img.chart {{ width: 100%; height: auto; }}
      </style>
      <title>Weather Report</title>
    </head>
    <body>
      <h1>Weather Report</h1>
      <div class="meta">
        <div><strong>{location_text}</strong></div>
        <div>Date Range: {start_ts} — {end_ts}</div>
        <div>Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</div>
      </div>
      <img class="chart" src="data:image/png;base64,{img_b64}" alt="Chart"/>
      <div class="footer">Data source: Open-Meteo (https://open-meteo.com)</div>
    </body>
    </html>
    """

    # Prefer WeasyPrint, fallback to Matplotlib PDF
    try:
        pdf_bytes = HTML(string=html).write_pdf()
    except Exception:
        from matplotlib.backends.backend_pdf import PdfPages

        buf = io.BytesIO()
        with PdfPages(buf) as pdf:
            # metadata page
            fig_meta = plt.figure(figsize=(8.27, 11.69))  # A4
            fig_meta.text(0.5, 0.9, "Weather Report", ha="center", fontsize=20)
            fig_meta.text(0.1, 0.8, f"Location: {location_text}", fontsize=12)
            fig_meta.text(0.1, 0.78, f"Date Range: {start_ts} — {end_ts}", fontsize=12)
            pdf.savefig(fig_meta)
            plt.close(fig_meta)

            # chart page
            fig_chart, ax = plt.subplots(figsize=(11, 5))
            ax.plot(df["timestamp"], df["temperature_2m"], label="Temperature (°C)")
            ax.plot(df["timestamp"], df["relative_humidity_2m"], label="Humidity (%)")
            ax.set_xlabel("Time (UTC)")
            ax.set_ylabel("Value")
            ax.set_title("Temperature & Humidity - Last 48 Hours")
            ax.legend()
            ax.grid(True, linestyle="--", linewidth=0.5)
            pdf.savefig(fig_chart)
            plt.close(fig_chart)

        buf.seek(0)
        pdf_bytes = buf.read()

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="weather_report.pdf",
    )


# --- Run ---
if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host=host, port=port, debug=debug)