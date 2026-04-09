#!/usr/bin/env python3
"""
CESAROPS Weather Service — Open-Meteo API Integration

Provides historical and forecast weather data to optimize satellite scan planning.
Used to filter out cloudy dates (optical useless) and check ice cover (SAR penetration).

Open-Meteo API: https://open-meteo.com/ (No API Key Required)
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional

def get_historical_weather(lat: float, lon: float, start_date: str, end_date: str) -> List[Dict]:
    """
    Get historical weather data for the scan area.
    Returns daily averages for cloud cover, wind speed, and estimated ice cover.
    
    :param lat: Latitude
    :param lon: Longitude
    :param start_date: 'YYYY-MM-DD'
    :param end_date: 'YYYY-MM-DD'
    :return: List of daily weather dicts
    """
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": [
            "cloud_cover_mean",
            "wind_speed_10m_mean",
            "precipitation_sum",
        ],
        "timezone": "UTC",
    }
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("daily", {})
        
        results = []
        for i in range(len(data.get("time", []))):
            results.append({
                "date": data["time"][i],
                "cloud_cover": data.get("cloud_cover_mean", [None]*len(data["time"]))[i],
                "wind_speed": data.get("wind_speed_10m_mean", [None]*len(data["time"]))[i],
                "precipitation": data.get("precipitation_sum", [None]*len(data["time"]))[i],
            })
        return results
    except Exception as e:
        print(f"⚠️ Weather API failed: {e}")
        return []

def filter_good_optical_dates(weather_data: List[Dict], max_cloud_cover: float = 20.0) -> List[str]:
    """
    Filter dates with low cloud cover for optical scanning.
    
    :param weather_data: Output from get_historical_weather
    :param max_cloud_cover: Threshold in percent (0-100)
    :return: List of 'YYYY-MM-DD' strings
    """
    good_dates = []
    for day in weather_data:
        if day["cloud_cover"] is not None and day["cloud_cover"] < max_cloud_cover:
            good_dates.append(day["date"])
    return good_dates

def check_ice_risk(lat: float, lon: float, month: int) -> str:
    """
    Simple heuristic for Great Lakes ice cover based on latitude and month.
    (Open-Meteo doesn't provide direct ice cover, but this helps planning).
    
    :param lat: Latitude
    :param lon: Longitude
    :param month: 1-12
    :return: 'low', 'moderate', 'high'
    """
    # Northern Great Lakes freeze earlier and thicker
    is_north = lat > 45.0
    is_lake = True  # Assume lake for this bbox
    
    if is_north:
        if month in [1, 2, 3]: return "high"  # Feb/Mar peak ice
        if month in [12, 4]: return "moderate"
        return "low"
    else:
        if month in [1, 2]: return "moderate"
        if month == 3: return "low"
        return "low"
