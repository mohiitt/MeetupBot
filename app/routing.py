"""Minimax cafe ranking using Google Distance Matrix.

Given employee coordinates and candidate cafes, fetch real driving times and
rank cafes so the worst-case commute is minimized. Internal ratings (>= 3 votes)
give the cafe up to a 20% effective-time discount.
"""

import os

import googlemaps
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
if not _API_KEY:
    raise RuntimeError("GOOGLE_MAPS_API_KEY is not set. Check your .env file.")

gmaps = googlemaps.Client(key=_API_KEY)


def rank_cafes_minimax(
    employee_coords: list[tuple],
    cafe_list: list[dict],
    db_ratings: dict,
) -> list[dict]:
    """Rank cafes by minimax fairness and return the top 5.

    - For each cafe, compute the MAX driving time across all employees.
    - Sort ascending by that max (lowest worst-case commute wins).
    - Cafes with an internal rating get up to a 20% effective-time discount.
    """
    if not employee_coords or not cafe_list:
        return []

    origins = [f"{lat},{lng}" for lat, lng in employee_coords]
    destinations = [f"{c['lat']},{c['lng']}" for c in cafe_list]

    matrix = gmaps.distance_matrix(
        origins=origins,
        destinations=destinations,
        mode="driving",
        units="imperial",
    )

    scored: list[dict] = []
    for j, cafe in enumerate(cafe_list):
        travel_times: list[int] = []
        for i in range(len(origins)):
            element = matrix["rows"][i]["elements"][j]
            if element["status"] == "OK":
                travel_times.append(element["duration"]["value"])  # seconds

        if not travel_times:
            continue

        max_secs = max(travel_times)
        avg_secs = sum(travel_times) / len(travel_times)

        internal_rating = db_ratings.get(cafe["place_id"])
        rating_multiplier = 1.0
        if internal_rating is not None:
            rating_multiplier = 1.0 - (internal_rating / 5.0) * 0.20

        effective_score = max_secs * rating_multiplier

        scored.append(
            {
                **cafe,
                "max_travel_seconds": max_secs,
                "avg_travel_seconds": avg_secs,
                "max_travel_minutes": round(max_secs / 60),
                "effective_score": effective_score,
                "internal_rating": internal_rating,
            }
        )

    scored.sort(key=lambda c: c["effective_score"])
    return scored[:5]
