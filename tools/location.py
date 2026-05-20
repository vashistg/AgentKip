import os

import httpx
import structlog
from pydantic import BaseModel

from models.goal import Location

logger = structlog.get_logger()

ELEVATION_API = "https://api.opentopodata.org/v1/aster30m"


class ElevationProfile(BaseModel):
    location: str          # city name for display
    altitude_m: float      # city altitude above sea level
    course_gain_m: float   # total elevation gain on the race course (if waypoints provided)


def get_city_altitude(location: Location) -> ElevationProfile:
    """Fetch the altitude above sea level for a city using its lat/lng."""
    log = logger.bind(tool="location.get_city_altitude", city=location.city)

    response = httpx.get(ELEVATION_API, params={
        "locations": f"{location.latitude},{location.longitude}",
    }, timeout=10)
    response.raise_for_status()
    data = response.json()

    altitude = data["results"][0]["elevation"]
    log.info("altitude_fetched", altitude_m=altitude)

    return ElevationProfile(
        location=location.city,
        altitude_m=altitude,
        course_gain_m=0,
    )


def get_course_elevation(waypoints: list[Location]) -> ElevationProfile:
    """
    Fetch total elevation gain for a race course defined by a list of waypoints.
    Waypoints should be sampled evenly along the course route.
    """
    if not waypoints:
        raise ValueError("At least one waypoint required")

    log = logger.bind(tool="location.get_course_elevation", waypoints=len(waypoints))

    locations_param = "|".join(f"{w.latitude},{w.longitude}" for w in waypoints)
    response = httpx.get(ELEVATION_API, params={"locations": locations_param}, timeout=15)
    response.raise_for_status()
    data = response.json()

    elevations = [r["elevation"] for r in data["results"]]
    gain = sum(
        max(0, elevations[i + 1] - elevations[i])
        for i in range(len(elevations) - 1)
    )

    log.info("course_elevation_fetched", total_gain_m=gain)

    return ElevationProfile(
        location=waypoints[0].city,
        altitude_m=elevations[0],
        course_gain_m=round(gain, 1),
    )
