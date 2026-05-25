"""Geocode a zip code or address to (lat, lng), and compute centroids.

Uses the googlemaps Python client with GOOGLE_MAPS_API_KEY from the environment.
"""

import os

import googlemaps
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
if not _API_KEY:
    raise RuntimeError("GOOGLE_MAPS_API_KEY is not set. Check your .env file.")

gmaps = googlemaps.Client(key=_API_KEY)


def geocode_input(zip_code: str | None = None, address: str | None = None) -> dict:
    """Geocode a zip code or address.

    Returns a dict with keys: lat, lng, neighborhood.
    Address takes priority over zip_code if both are provided.
    Raises ValueError if neither is provided or the query has no results.
    """
    query = address if address else zip_code
    if not query:
        raise ValueError("Must provide zip_code or address")

    result = gmaps.geocode(query)
    if not result:
        raise ValueError(f"Could not geocode: {query}")

    loc = result[0]["geometry"]["location"]
    neighborhood = extract_neighborhood(result[0]["address_components"])

    return {
        "lat": loc["lat"],
        "lng": loc["lng"],
        "neighborhood": neighborhood,
    }


def extract_neighborhood(components: list) -> str:
    """Pick the most useful human-readable area label from geocode components.

    Falls through neighborhood -> locality -> sublocality.
    """
    for c in components:
        if "neighborhood" in c["types"]:
            return c["long_name"]
    for c in components:
        if "locality" in c["types"]:
            return c["long_name"]
    for c in components:
        if "sublocality" in c["types"]:
            return c["long_name"]
    return "Unknown area"


def compute_centroid(coords: list[tuple]) -> tuple:
    """Geometric centroid (avg lat, avg lng) of a list of (lat, lng) tuples.

    Used as the starting search point for cafe discovery.
    """
    if not coords:
        raise ValueError("No coordinates provided")
    avg_lat = sum(c[0] for c in coords) / len(coords)
    avg_lng = sum(c[1] for c in coords) / len(coords)
    return (avg_lat, avg_lng)
