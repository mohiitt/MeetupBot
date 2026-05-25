"""Search for cafes near a (lat, lng) centroid using Google Places API."""

import os

import googlemaps
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
if not _API_KEY:
    raise RuntimeError("GOOGLE_MAPS_API_KEY is not set. Check your .env file.")

gmaps = googlemaps.Client(key=_API_KEY)


def find_nearby_cafes(centroid: tuple, radius_meters: int = 5000) -> list[dict]:
    """Return up to 10 cafes near the centroid, sorted by Google's prominence.

    Each cafe dict has: place_id, name, address, lat, lng, google_rating.
    """
    lat, lng = centroid
    results = gmaps.places_nearby(
        location=(lat, lng),
        radius=radius_meters,
        type="cafe",
    )

    cafes: list[dict] = []
    for place in results.get("results", [])[:10]:
        loc = place["geometry"]["location"]
        cafes.append(
            {
                "place_id": place["place_id"],
                "name": place["name"],
                "address": place.get("vicinity", ""),
                "lat": loc["lat"],
                "lng": loc["lng"],
                "google_rating": place.get("rating"),
            }
        )

    return cafes
