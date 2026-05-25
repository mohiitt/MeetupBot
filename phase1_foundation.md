# Attend Meetup Bot — Phase 1: Foundation & Core Engine

**Goal:** Project is set up, database is running, locations can be saved, and the minimax cafe recommendation engine works end-to-end. By the end of this phase you can run `/meetup-location` and `/meetup-find` in Slack and get real cafe recommendations.

---

## Step 1 — Slack App setup (before writing any code)

1. Go to https://api.slack.com/apps → Create New App → From scratch
2. Name it `MeetupBot`, pick your workspace
3. Under **OAuth & Permissions** → add these Bot Token Scopes:
   - `commands`
   - `chat:write`
   - `users:read`
4. Under **Slash Commands** → create three commands now (URLs will be filled later):
   - `/meetup-location`
   - `/meetup-find`
   - `/meetup-status`
5. Under **Interactivity & Shortcuts** → toggle ON (URL to be filled after ngrok is running)
6. Install the app to your workspace → copy **Bot User OAuth Token** and **Signing Secret** into `.env`

---

## Step 2 — Project scaffold

Create this folder structure in Cursor:

```
attend-meetup-bot/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── slack_handlers.py
│   ├── location.py
│   ├── cafes.py
│   ├── routing.py
│   └── database.py
├── .env
├── .gitignore
└── requirements.txt
```

**`.gitignore`:**

```
.env
__pycache__/
*.pyc
.venv/
```

**`requirements.txt`:**

```
fastapi
uvicorn
slack-bolt
slack-sdk
googlemaps
sqlalchemy
psycopg2-binary
python-dotenv
httpx
```

**`.env`:**

```
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
GOOGLE_MAPS_API_KEY=
DATABASE_URL=postgresql://localhost/meetupbot
```

Run in terminal:

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Step 3 — Google Cloud setup (one-time)

1. Go to https://console.cloud.google.com → New Project → `attend-meetup-bot`
2. Enable these three APIs (search each by name):
   - **Geocoding API**
   - **Places API**
   - **Distance Matrix API**
3. Go to **Credentials** → Create Credentials → API Key → copy into `.env`
4. Optionally restrict the key to only those 3 APIs for security

> All three share one key. At 1–2 meetup requests/week you will use roughly $1–2/month of the $200 free monthly credit.

---

## Step 4 — Database models (`app/database.py`)

```python
# TASK: SQLAlchemy models and DB session for the meetup bot.
# Three tables: employees, cafes, ratings.
# Uses environment variable DATABASE_URL for connection.

import os
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Employee(Base):
    __tablename__ = "employees"

    slack_user_id  = Column(String, primary_key=True)
    display_name   = Column(String)
    zip_code       = Column(String, nullable=True)
    address        = Column(String, nullable=True)
    lat            = Column(Float)
    lng            = Column(Float)
    neighborhood   = Column(String, nullable=True)   # human-readable city/area
    updated_at     = Column(DateTime, default=datetime.utcnow)


class Cafe(Base):
    __tablename__ = "cafes"

    place_id             = Column(String, primary_key=True)
    name                 = Column(String)
    address              = Column(String)
    lat                  = Column(Float)
    lng                  = Column(Float)
    google_rating        = Column(Float, nullable=True)
    total_votes          = Column(Integer, default=0)
    avg_internal_rating  = Column(Float, nullable=True)


class Rating(Base):
    __tablename__ = "ratings"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    slack_user_id  = Column(String)
    place_id       = Column(String)
    score          = Column(Integer)           # 1–5
    comment        = Column(Text, nullable=True)
    rated_at       = Column(DateTime, default=datetime.utcnow)


def create_tables():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

Create the local Postgres database then run table creation:

```bash
createdb meetupbot          # or use pgAdmin / Railway DB
python -c "from app.database import create_tables; create_tables()"
```

---

## Step 5 — Location resolver (`app/location.py`)

```python
# TASK: Geocode a zip code or address string to (lat, lng).
# Also computes the geometric centroid of a list of coordinates.
# Uses the googlemaps Python client with GOOGLE_MAPS_API_KEY.

import os
import googlemaps
from dotenv import load_dotenv

load_dotenv()
gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY"))


def geocode_input(zip_code: str = None, address: str = None) -> dict:
    """
    Geocode zip code or address. Returns dict with lat, lng, neighborhood.
    Address takes priority over zip_code if both provided.
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
        "neighborhood": neighborhood
    }


def extract_neighborhood(components: list) -> str:
    """Extract a human-readable city/neighborhood label from geocode components."""
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
    """
    Returns the geometric centroid (avg lat, avg lng) of a list of (lat, lng) tuples.
    Used as the starting search point for cafe discovery.
    """
    if not coords:
        raise ValueError("No coordinates provided")
    avg_lat = sum(c[0] for c in coords) / len(coords)
    avg_lng = sum(c[1] for c in coords) / len(coords)
    return (avg_lat, avg_lng)
```

---

## Step 6 — Cafe finder (`app/cafes.py`)

```python
# TASK: Search for cafes near a (lat, lng) centroid using Google Places API.
# Returns up to 10 cafes as a list of dicts with place_id, name, address, lat, lng, google_rating.

import os
import googlemaps
from dotenv import load_dotenv

load_dotenv()
gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY"))


def find_nearby_cafes(centroid: tuple, radius_meters: int = 5000) -> list[dict]:
    """
    Search for cafes near the centroid point.
    Returns top 10 results sorted by Google's prominence ranking.
    """
    lat, lng = centroid
    results = gmaps.places_nearby(
        location=(lat, lng),
        radius=radius_meters,
        type="cafe",
        rank_by=None      # use radius mode (not distance) to keep radius filter active
    )

    cafes = []
    for place in results.get("results", [])[:10]:
        loc = place["geometry"]["location"]
        cafes.append({
            "place_id":      place["place_id"],
            "name":          place["name"],
            "address":       place.get("vicinity", ""),
            "lat":           loc["lat"],
            "lng":           loc["lng"],
            "google_rating": place.get("rating", None),
        })

    return cafes
```

---

## Step 7 — Minimax routing engine (`app/routing.py`)

```python
# TASK: Given employee coordinates and a list of candidate cafes,
# call Google Distance Matrix API to get real driving times,
# then rank cafes by minimizing the maximum travel time any one employee has.
# Also blends in internal ratings if a cafe has >= 3 votes.

import os
import googlemaps
from dotenv import load_dotenv

load_dotenv()
gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY"))


def rank_cafes_minimax(
    employee_coords: list[tuple],
    cafe_list: list[dict],
    db_ratings: dict   # {place_id: avg_internal_rating} for cafes with >= 3 votes
) -> list[dict]:
    """
    Ranks cafes using minimax fairness:
    - For each cafe, find the MAXIMUM travel time among all employees
    - Sort ascending by that max (fairest cafe = shortest worst-case commute)
    - Apply a 20% rating boost for cafes with >= 3 internal ratings
    - Return top 3
    """
    if not employee_coords or not cafe_list:
        return []

    origins = [f"{lat},{lng}" for lat, lng in employee_coords]
    destinations = [f"{c['lat']},{c['lng']}" for c in cafe_list]

    matrix = gmaps.distance_matrix(
        origins=origins,
        destinations=destinations,
        mode="driving",
        units="imperial"
    )

    scored = []
    for j, cafe in enumerate(cafe_list):
        travel_times = []
        for i in range(len(origins)):
            element = matrix["rows"][i]["elements"][j]
            if element["status"] == "OK":
                travel_times.append(element["duration"]["value"])  # seconds

        if not travel_times:
            continue

        max_secs = max(travel_times)
        avg_secs = sum(travel_times) / len(travel_times)

        # Rating blend: if cafe has internal ratings, reduce effective max_secs by up to 20%
        internal_rating = db_ratings.get(cafe["place_id"])
        rating_multiplier = 1.0
        if internal_rating is not None:
            rating_multiplier = 1.0 - (internal_rating / 5.0) * 0.20

        effective_score = max_secs * rating_multiplier

        scored.append({
            **cafe,
            "max_travel_seconds": max_secs,
            "avg_travel_seconds": avg_secs,
            "max_travel_minutes": round(max_secs / 60),
            "effective_score":    effective_score,
            "internal_rating":    internal_rating,
        })

    scored.sort(key=lambda c: c["effective_score"])
    return scored[:3]
```

---

## Step 8 — Slash command handlers (`app/slack_handlers.py`)

```python
# TASK: Register /meetup-location, /meetup-find, and /meetup-status
# slash commands using Slack Bolt for Python.
# Each handler uses the modules built in Steps 5-7.

import os
from slack_bolt import App
from slack_sdk import WebClient
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from .database import SessionLocal, Employee, Cafe
from .location import geocode_input, compute_centroid
from .cafes import find_nearby_cafes
from .routing import rank_cafes_minimax

load_dotenv()

app = App(
    token=os.getenv("SLACK_BOT_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET")
)


# ── /meetup-location ──────────────────────────────────────────────────────────

@app.command("/meetup-location")
def handle_meetup_location(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "location_modal",
            "title": {"type": "plain_text", "text": "Your location"},
            "submit": {"type": "plain_text", "text": "Save"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "zip_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "zip_input",
                        "placeholder": {"type": "plain_text", "text": "e.g. 94105"}
                    },
                    "label": {"type": "plain_text", "text": "Zip code"}
                },
                {
                    "type": "input",
                    "block_id": "address_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "address_input",
                        "placeholder": {"type": "plain_text", "text": "e.g. 123 Main St, San Jose CA"}
                    },
                    "label": {"type": "plain_text", "text": "Home address (overrides zip if filled)"}
                }
            ]
        }
    )


@app.view("location_modal")
def handle_location_submission(ack, body, client):
    ack()
    values = body["view"]["state"]["values"]
    zip_code = values["zip_block"]["zip_input"]["value"]
    address  = values["address_block"]["address_input"]["value"]
    user_id  = body["user"]["id"]
    username = body["user"]["username"]

    try:
        geo = geocode_input(zip_code=zip_code, address=address)
    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"Couldn't geocode that location: {e}. Try a different zip or address."
        )
        return

    db: Session = SessionLocal()
    try:
        employee = db.query(Employee).filter_by(slack_user_id=user_id).first()
        if employee:
            employee.lat           = geo["lat"]
            employee.lng           = geo["lng"]
            employee.neighborhood  = geo["neighborhood"]
            employee.zip_code      = zip_code
            employee.address       = address
            employee.display_name  = username
        else:
            db.add(Employee(
                slack_user_id = user_id,
                display_name  = username,
                zip_code      = zip_code,
                address       = address,
                lat           = geo["lat"],
                lng           = geo["lng"],
                neighborhood  = geo["neighborhood"]
            ))
        db.commit()
    finally:
        db.close()

    client.chat_postMessage(
        channel=user_id,
        text=f"Got it! Location saved as *{geo['neighborhood']}*. You'll be included in the next `/meetup-find`."
    )


# ── /meetup-find ──────────────────────────────────────────────────────────────

@app.command("/meetup-find")
def handle_meetup_find(ack, body, respond):
    ack()
    respond("Finding the best cafes for your team... give me a moment ☕")

    db: Session = SessionLocal()
    try:
        employees = db.query(Employee).all()
        if len(employees) < 2:
            respond("Need at least 2 employees with saved locations. Use `/meetup-location` first.")
            return

        coords = [(e.lat, e.lng) for e in employees]
        centroid = compute_centroid(coords)
        cafes = find_nearby_cafes(centroid)

        if not cafes:
            respond("No cafes found near the team midpoint. Try again or check the map area.")
            return

        # Pull internal ratings for cafes that have >= 3 votes
        from .database import Cafe as CafeModel
        db_cafes = {c.place_id: c for c in db.query(CafeModel).all()}
        db_ratings = {
            pid: c.avg_internal_rating
            for pid, c in db_cafes.items()
            if c.total_votes >= 3 and c.avg_internal_rating is not None
        }

        ranked = rank_cafes_minimax(coords, cafes, db_ratings)

    finally:
        db.close()

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "☕ Top 3 meetup cafes for your team"}
        }
    ]

    medals = ["🥇", "🥈", "🥉"]
    for i, cafe in enumerate(ranked):
        g_rating = f"⭐ {cafe['google_rating']}" if cafe.get("google_rating") else "No Google rating"
        i_rating = f"  ·  🏠 {cafe['internal_rating']:.1f}/5 internal" if cafe.get("internal_rating") else ""
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{medals[i]} *{cafe['name']}*\n"
                    f"{cafe['address']}\n"
                    f"{g_rating}{i_rating}\n"
                    f"🕐 Max commute: *{cafe['max_travel_minutes']} min*"
                )
            }
        })
        blocks.append({"type": "divider"})

    respond(blocks=blocks)


# ── /meetup-status ────────────────────────────────────────────────────────────

@app.command("/meetup-status")
def handle_meetup_status(ack, respond):
    ack()
    db: Session = SessionLocal()
    try:
        employees = db.query(Employee).all()
    finally:
        db.close()

    if not employees:
        respond("No locations saved yet. Ask everyone to run `/meetup-location`.")
        return

    lines = [f"*{len(employees)} team members have saved locations:*\n"]
    for e in employees:
        lines.append(f"• @{e.display_name} — {e.neighborhood}")

    respond("\n".join(lines))
```

---

## Step 9 — FastAPI entry point (`app/main.py`)

```python
# TASK: Mount Slack Bolt app onto FastAPI using SlackRequestHandler.
# Single POST endpoint at /slack/events handles all Slack traffic.

from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi import SlackRequestHandler
from .slack_handlers import app as slack_app

api = FastAPI()
handler = SlackRequestHandler(slack_app)


@api.post("/slack/events")
async def slack_events(req: Request):
    return await handler.handle(req)
```

---

## Step 10 — Local run + Slack wiring

**Terminal 1 — start the server:**

```bash
uvicorn app.main:api --reload --port 3000
```

**Terminal 2 — start ngrok:**

```bash
ngrok http 3000
```

Copy the `https://xxxx.ngrok.io` URL.

**Back in Slack App settings:**

- Interactivity & Shortcuts → Request URL: `https://xxxx.ngrok.io/slack/events`
- Each Slash Command → Request URL: `https://xxxx.ngrok.io/slack/events`

**Test sequence:**

1. `/meetup-location` → fill in a zip code → confirm "Location saved"
2. Repeat from a second Slack account or ask a teammate
3. `/meetup-status` → verify both locations appear
4. `/meetup-find` → confirm 3 cafes appear with travel times

---

## Phase 1 done when:

- [ ] `/meetup-location` saves location to DB
- [ ] `/meetup-status` lists all saved employees
- [ ] `/meetup-find` returns 3 real cafes with real driving-time estimates
- [ ] No crashes on happy path

**Once all four boxes are checked, move to Phase 2.**
