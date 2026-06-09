"""
owm_client.py
OpenWeatherMap API Client for Dhanbad AQI System
Free tier: 1,000 calls/day | 60 calls/min

Fetches:
  - Current weather
  - Air pollution (PM2.5, PM10, CO, NO2, O3, SO2, NH3 directly!)
  - 5-day / 3-hour forecast (free tier)
"""

import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from functools import lru_cache

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Dhanbad, Jharkhand coordinates
DHANBAD_LAT = 23.7957
DHANBAD_LON = 86.4304

# Get from environment variable (never hardcode secrets)
OWM_API_KEY = os.environ.get("OWM_API_KEY")

BASE_URL = "https://api.openweathermap.org"

HEADERS = {"Accept": "application/json"}


def _get(endpoint: str, params: dict) -> dict:
    """Generic GET with error handling and basic retry."""
    if not OWM_API_KEY:
        raise RuntimeError("OWM_API_KEY is not set. Add it to your environment or .env file.")
    params["appid"] = OWM_API_KEY
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{BASE_URL}{endpoint}",
                params=params,
                headers=HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:  # rate limit
                print(f"  Rate limited. Waiting 60s... (attempt {attempt+1})")
                time.sleep(61)
            else:
                raise e
        except requests.exceptions.ConnectionError:
            print(f"  Connection error. Retrying in 5s... (attempt {attempt+1})")
            time.sleep(5)
    raise RuntimeError("OWM API request failed after 3 attempts")


# ──────────────────────────────────────────────
# Current Conditions
# ──────────────────────────────────────────────

def get_current_weather(lat: float = DHANBAD_LAT, lon: float = DHANBAD_LON) -> dict:
    """
    Fetch current weather. Returns raw OWM response dict.
    Keys: main.temp, main.humidity, wind.speed, wind.deg, weather[0].description
    Temperatures are in Kelvin by default; use units=metric for Celsius.
    """
    data = _get("/data/2.5/weather", {
        "lat":   lat,
        "lon":   lon,
        "units": "metric",   # Celsius
    })
    return data


def get_current_air_pollution(lat: float = DHANBAD_LAT, lon: float = DHANBAD_LON) -> dict:
    """
    Fetch current air pollution from OWM Air Pollution API (FREE).
    Returns PM2.5, PM10, CO, NO, NO2, O3, SO2, NH3 directly.
    OWM AQI: 1=Good, 2=Fair, 3=Moderate, 4=Poor, 5=Very Poor
    """
    data = _get("/data/2.5/air_pollution", {
        "lat": lat,
        "lon": lon,
    })
    return data


# ──────────────────────────────────────────────
# 5-Day Forecast (free tier: 3-hour intervals)
# ──────────────────────────────────────────────

def get_forecast_weather(lat: float = DHANBAD_LAT, lon: float = DHANBAD_LON) -> pd.DataFrame:
    """
    Fetch 5-day / 3-hour weather forecast.
    Returns a DataFrame with columns matching our feature schema.
    """
    data = _get("/data/2.5/forecast", {
        "lat":   lat,
        "lon":   lon,
        "units": "metric",
    })
    rows = []
    for item in data.get("list", []):
        weather = item.get("weather", [{}])[0]
        rows.append({
            "Timestamp":   pd.to_datetime(item["dt"], unit="s"),
            "Temperature": item["main"]["temp"],
            "Humidity":    item["main"]["humidity"],
            "WindSpeed":   item["wind"]["speed"],
            "WindDeg":     item["wind"].get("deg", 0),
            "Pressure":    item["main"]["pressure"],
            "Clouds":      item.get("clouds", {}).get("all", 0),
            "Rain3h":      item.get("rain", {}).get("3h", 0),
            "WeatherId":   weather.get("id"),
            "WeatherMain": weather.get("main"),
            "WeatherDesc": weather.get("description"),
            "WeatherPod":  item.get("sys", {}).get("pod", "d"),
        })
    return pd.DataFrame(rows)


def get_daily_weather_forecast(lat: float = DHANBAD_LAT, lon: float = DHANBAD_LON, days: int = 5) -> list[dict]:
    """
    5-day daily weather forecast using FREE OWM /data/2.5/forecast endpoint.
    Aggregates 3-hour slots into daily summaries. No paid API needed.
    """
    from collections import defaultdict

    data = _get("/data/2.5/forecast", {
        "lat":   lat,
        "lon":   lon,
        "units": "metric",
    })

    daily = defaultdict(list)
    for item in data.get("list", []):
        date = pd.to_datetime(item["dt"], unit="s").date().isoformat()
        daily[date].append(item)

    forecasts = []
    for date in sorted(daily.keys())[:days]:
        slots    = daily[date]
        temps    = [s["main"]["temp"] for s in slots]
        humidity = [s["main"]["humidity"] for s in slots]
        pressure = [s["main"]["pressure"] for s in slots]
        wind     = [s["wind"]["speed"] for s in slots]
        clouds   = [s.get("clouds", {}).get("all", 0) for s in slots]
        rain     = sum(s.get("rain", {}).get("3h", 0) for s in slots)

        weather_slots = [s.get("weather", [{}])[0] for s in slots]
        main_weather  = max(
            set(w.get("main", "") for w in weather_slots),
            key=lambda x: sum(1 for w in weather_slots if w.get("main") == x)
        )
        desc_weather = max(
            set(w.get("description", "") for w in weather_slots),
            key=lambda x: sum(1 for w in weather_slots if w.get("description") == x)
        )
        icon = next((w.get("icon") for w in weather_slots if w.get("main") == main_weather), None)
        wid  = next((w.get("id")   for w in weather_slots if w.get("main") == main_weather), None)

        forecasts.append({
            "date":         date,
            "timestamp":    f"{date}T12:00:00",
            "temp_day":     round(sum(temps) / len(temps), 1),
            "temp_min":     round(min(temps), 1),
            "temp_max":     round(max(temps), 1),
            "humidity":     round(sum(humidity) / len(humidity), 1),
            "pressure":     round(sum(pressure) / len(pressure), 1),
            "wind_speed":   round(sum(wind) / len(wind), 2),
            "clouds":       round(sum(clouds) / len(clouds), 1),
            "rain":         round(rain, 2),
            "weather_id":   wid,
            "weather_main": main_weather,
            "weather_desc": desc_weather,
            "weather_icon": icon,
        })

    return forecasts

def get_forecast_air_pollution(lat: float = DHANBAD_LAT, lon: float = DHANBAD_LON) -> pd.DataFrame:
    """
    Fetch 5-day air pollution forecast (FREE on OWM).
    Returns DataFrame with PM2.5, PM10, NO2, O3, CO, SO2, NH3.
    """
    data = _get("/data/2.5/air_pollution/forecast", {
        "lat": lat,
        "lon": lon,
    })
    rows = []
    for item in data.get("list", []):
        comp = item.get("components", {})
        rows.append({
            "Timestamp": pd.to_datetime(item["dt"], unit="s"),
            "CO":        comp.get("co",   None),
            "NO":        comp.get("no",   None),
            "NO2":       comp.get("no2",  None),
            "O3":        comp.get("o3",   None),
            "SO2":       comp.get("so2",  None),
            "NH3":       comp.get("nh3",  None),
            "PM2_5":     comp.get("pm2_5", None),
            "PM10":      comp.get("pm10",  None),
        })
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# Merged Snapshot for Inference
# ──────────────────────────────────────────────

def get_current_snapshot() -> dict:
    """
    Fetch and merge current weather + air quality into a single dict
    for immediate model inference.
    """
    weather = get_current_weather()
    air     = get_current_air_pollution()

    comp = air.get("list", [{}])[0].get("components", {})

    snapshot = {
        "timestamp":   datetime.utcnow().isoformat(),
        "temperature": weather["main"]["temp"],
        "humidity":    weather["main"]["humidity"],
        "wind_speed":  weather["wind"]["speed"],
        "wind_deg":    weather["wind"].get("deg", 0),
        "pressure":    weather["main"]["pressure"],
        "weather_desc": weather["weather"][0]["description"],
        # Pollutants (µg/m³ except CO which is µg/m³ too in OWM)
        "co":    comp.get("co"),
        "no":    comp.get("no"),
        "no2":   comp.get("no2"),
        "o3":    comp.get("o3"),
        "so2":   comp.get("so2"),
        "nh3":   comp.get("nh3"),
        "pm2_5": comp.get("pm2_5"),
        "pm10":  comp.get("pm10"),
        "owm_aqi": air.get("list", [{}])[0].get("main", {}).get("aqi"),
    }
    return snapshot


def get_merged_forecast() -> pd.DataFrame:
    """
    Merge weather + air pollution forecast into one DataFrame.
    Used for generating 5-day AQI predictions.
    """
    weather_df = get_forecast_weather()
    air_df     = get_forecast_air_pollution()

    merged = pd.merge(weather_df, air_df, on="Timestamp", how="inner")
    return merged.sort_values("Timestamp").reset_index(drop=True)


# ──────────────────────────────────────────────
# Utility: map OWM AQI (1-5) to CPCB category
# ──────────────────────────────────────────────

OWM_TO_LABEL = {
    1: "Good",
    2: "Fair",
    3: "Moderate",
    4: "Poor",
    5: "Very Poor",
}

US_AQI_BREAKPOINTS = [50, 100, 150, 200, 300, 500]
US_AQI_LABELS      = ["Good", "Moderate", "Unhealthy for Sensitive Groups", "Unhealthy", "Very Unhealthy", "Hazardous"]


def aqi_to_category(aqi: float) -> str:
    for bp, label in zip(US_AQI_BREAKPOINTS, US_AQI_LABELS):
        if aqi <= bp:
            return label
    return "Hazardous"

def aqi_health_message(aqi: float) -> str:
    cat = aqi_to_category(aqi)
    messages = {
        "Good":                              "Air quality is good. Safe for all outdoor activities.",
        "Moderate":                          "Unusually sensitive people should consider limiting prolonged outdoor exertion.",
        "Unhealthy for Sensitive Groups":    "Children, elderly, and people with heart/lung disease should reduce outdoor activity.",
        "Unhealthy":                         "Everyone may experience health effects. Limit prolonged outdoor exertion.",
        "Very Unhealthy":                    "Health alert — everyone should avoid prolonged outdoor activity.",
        "Hazardous":                         "HAZARDOUS. Emergency conditions. Everyone should avoid all outdoor activity.",
    }
    return f"[{cat}] {messages.get(cat, '')}"

if __name__ == "__main__":
    # Quick test — replace with your API key in env
    print("Testing OWM client for Dhanbad...")
    snap = get_current_snapshot()
    print("Current snapshot:")
    for k, v in snap.items():
        print(f"  {k}: {v}")
