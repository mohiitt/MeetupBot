"""Search for cafes near a (lat, lng) centroid using Google Places API."""

import os

import googlemaps
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
if not _API_KEY:
    raise RuntimeError("GOOGLE_MAPS_API_KEY is not set. Check your .env file.")

gmaps = googlemaps.Client(key=_API_KEY)


def find_nearby_cafes(centroid: tuple, radius_meters: int = 15000) -> list[dict]:
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


def upsert_cafes_to_db(db, cafe_list: list[dict]) -> None:
    """Save discovered cafes to the DB for future ratings.

    Inserts new cafes and refreshes basic Google metadata for existing cafes
    without touching accumulated internal ratings.
    """
    from .database import Cafe

    for cafe in cafe_list:
        existing = db.query(Cafe).filter_by(place_id=cafe["place_id"]).first()
        if existing:
            existing.name = cafe["name"]
            existing.address = cafe["address"]
            existing.lat = cafe["lat"]
            existing.lng = cafe["lng"]
            existing.google_rating = cafe.get("google_rating")
            continue

        db.add(
            Cafe(
                place_id=cafe["place_id"],
                name=cafe["name"],
                address=cafe["address"],
                lat=cafe["lat"],
                lng=cafe["lng"],
                google_rating=cafe.get("google_rating"),
                total_votes=0,
            )
        )

    db.commit()
