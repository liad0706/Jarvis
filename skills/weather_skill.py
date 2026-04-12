"""Weather skill — current conditions and forecasts via Open-Meteo (no API key needed)."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import httpx

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"

# WMO Weather interpretation codes → Hebrew descriptions
WMO_HEBREW: dict[int, str] = {
    0: "שמיים בהירים",
    1: "בעיקר בהיר",
    2: "מעונן חלקית",
    3: "מעונן",
    45: "ערפל",
    48: "ערפל קפוא",
    51: "טפטוף קל",
    53: "טפטוף מתון",
    55: "טפטוף כבד",
    56: "טפטוף קפוא קל",
    57: "טפטוף קפוא כבד",
    61: "גשם קל",
    63: "גשם מתון",
    65: "גשם כבד",
    66: "גשם קפוא קל",
    67: "גשם קפוא כבד",
    71: "שלג קל",
    73: "שלג מתון",
    75: "שלג כבד",
    77: "גרגרי שלג",
    80: "מקלחות גשם קלות",
    81: "מקלחות גשם מתונות",
    82: "מקלחות גשם סוערות",
    85: "מקלחות שלג קלות",
    86: "מקלחות שלג כבדות",
    95: "סופת רעמים",
    96: "סופת רעמים עם ברד קל",
    99: "סופת רעמים עם ברד כבד",
}

DAYS_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]


def _wmo(code: int) -> str:
    return WMO_HEBREW.get(code, f"קוד מזג אוויר {code}")


def _day_name(d: date) -> str:
    return DAYS_HE[d.weekday()]


class WeatherSkill(BaseSkill):
    name = "weather"
    description = (
        "Get current weather and forecasts. "
        "Actions: current, today, tomorrow, weekly, should_i_take_umbrella."
    )

    RISK_MAP = {
        "current": "low",
        "today": "low",
        "tomorrow": "low",
        "weekly": "low",
        "should_i_take_umbrella": "low",
    }

    def __init__(self):
        self._lat = float(os.environ.get("WEATHER_LATITUDE", "32.0853"))
        self._lon = float(os.environ.get("WEATHER_LONGITUDE", "34.7818"))

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("weather.%s failed", action)
            return {"error": str(e)}

    async def _fetch(self, extra_params: dict) -> dict:
        params = {
            "latitude": self._lat,
            "longitude": self._lon,
            "timezone": "auto",
            **extra_params,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(BASE_URL, params=params)
            r.raise_for_status()
            return r.json()

    async def do_current(self) -> dict:
        """Current weather: temperature, description, wind, humidity, feels like."""
        data = await self._fetch({
            "current": [
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "wind_speed_10m",
                "weather_code",
            ],
        })
        c = data["current"]
        temp = c["temperature_2m"]
        feels = c["apparent_temperature"]
        humidity = c["relative_humidity_2m"]
        wind = c["wind_speed_10m"]
        desc = _wmo(c["weather_code"])
        units = data.get("current_units", {})
        temp_u = units.get("temperature_2m", "°C")
        wind_u = units.get("wind_speed_10m", "km/h")

        reply = (
            f"מזג האוויר כרגע: {desc}\n"
            f"טמפרטורה: {temp}{temp_u} (מורגש {feels}{temp_u})\n"
            f"לחות: {humidity}%\n"
            f"רוח: {wind} {wind_u}"
        )
        return {
            "temperature": temp,
            "feels_like": feels,
            "humidity": humidity,
            "wind_speed": wind,
            "description": desc,
            "reply_to_user_hebrew": reply,
        }

    async def do_today(self) -> dict:
        """Today's forecast: high/low, rain chance, sunrise/sunset."""
        data = await self._fetch({
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "weather_code",
                "sunrise",
                "sunset",
            ],
        })
        d = data["daily"]
        idx = 0
        high = d["temperature_2m_max"][idx]
        low = d["temperature_2m_min"][idx]
        rain = d["precipitation_probability_max"][idx]
        desc = _wmo(d["weather_code"][idx])
        sunrise = d["sunrise"][idx].split("T")[1] if "T" in d["sunrise"][idx] else d["sunrise"][idx]
        sunset = d["sunset"][idx].split("T")[1] if "T" in d["sunset"][idx] else d["sunset"][idx]

        reply = (
            f"תחזית להיום: {desc}\n"
            f"מקסימום: {high}°C, מינימום: {low}°C\n"
            f"סיכוי גשם: {rain}%\n"
            f"זריחה: {sunrise}, שקיעה: {sunset}"
        )
        return {
            "high": high,
            "low": low,
            "rain_chance": rain,
            "description": desc,
            "sunrise": sunrise,
            "sunset": sunset,
            "reply_to_user_hebrew": reply,
        }

    async def do_tomorrow(self) -> dict:
        """Tomorrow's forecast: high/low, rain chance, description."""
        data = await self._fetch({
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "weather_code",
                "sunrise",
                "sunset",
            ],
        })
        d = data["daily"]
        idx = 1
        high = d["temperature_2m_max"][idx]
        low = d["temperature_2m_min"][idx]
        rain = d["precipitation_probability_max"][idx]
        desc = _wmo(d["weather_code"][idx])
        sunrise = d["sunrise"][idx].split("T")[1] if "T" in d["sunrise"][idx] else d["sunrise"][idx]
        sunset = d["sunset"][idx].split("T")[1] if "T" in d["sunset"][idx] else d["sunset"][idx]

        reply = (
            f"תחזית למחר: {desc}\n"
            f"מקסימום: {high}°C, מינימום: {low}°C\n"
            f"סיכוי גשם: {rain}%\n"
            f"זריחה: {sunrise}, שקיעה: {sunset}"
        )
        return {
            "high": high,
            "low": low,
            "rain_chance": rain,
            "description": desc,
            "sunrise": sunrise,
            "sunset": sunset,
            "reply_to_user_hebrew": reply,
        }

    async def do_weekly(self) -> dict:
        """7-day forecast summary."""
        data = await self._fetch({
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "weather_code",
            ],
        })
        d = data["daily"]
        days_data = []
        lines = ["תחזית שבועית:"]
        today = date.today()

        for i in range(min(7, len(d["weather_code"]))):
            day_date = today + timedelta(days=i)
            day_name = "היום" if i == 0 else ("מחר" if i == 1 else f"יום {_day_name(day_date)}")
            high = d["temperature_2m_max"][i]
            low = d["temperature_2m_min"][i]
            rain = d["precipitation_probability_max"][i]
            desc = _wmo(d["weather_code"][i])
            days_data.append({
                "day": day_name,
                "date": day_date.isoformat(),
                "high": high,
                "low": low,
                "rain_chance": rain,
                "description": desc,
            })
            lines.append(f"• {day_name}: {desc}, {low}–{high}°C, גשם {rain}%")

        return {
            "days": days_data,
            "reply_to_user_hebrew": "\n".join(lines),
        }

    async def do_should_i_take_umbrella(self) -> dict:
        """Simple yes/no: should I take an umbrella today?"""
        data = await self._fetch({
            "daily": ["precipitation_probability_max", "weather_code"],
        })
        rain = data["daily"]["precipitation_probability_max"][0]
        wmo_code = data["daily"]["weather_code"][0]
        # Rain codes: 51-67 (drizzle/rain), 80-82 (showers), 95-99 (thunderstorm)
        is_rainy_code = wmo_code in range(51, 68) or wmo_code in range(80, 83) or wmo_code in range(95, 100)
        take_umbrella = rain >= 40 or is_rainy_code

        if take_umbrella:
            reply = f"כן, כדאי לקחת מטרייה — סיכוי גשם {rain}%."
        else:
            reply = f"לא צריך מטרייה היום — סיכוי גשם נמוך ({rain}%)."

        return {
            "take_umbrella": take_umbrella,
            "rain_chance": rain,
            "reply_to_user_hebrew": reply,
        }
