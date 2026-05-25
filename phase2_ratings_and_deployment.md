# Attend Meetup Bot — Phase 2: Ratings, Polish & Deployment

**Goal:** Add the post-meetup rating system, improve the Slack UI with Block Kit, protect the app with error handling, and deploy to a permanent public URL so the bot works without your laptop running.

**Prerequisite:** Phase 1 is complete. `/meetup-location`, `/meetup-status`, and `/meetup-find` all work locally.

---

## Step 1 — Ratings module (`app/ratings.py`)

```python
# TASK: Write and read cafe ratings.
# write_rating() saves a new rating row and recomputes the cafe's average.
# get_ratings_map() returns a dict of {place_id: avg_internal_rating}
# for all cafes that have >= min_votes ratings.

from sqlalchemy.orm import Session
from .database import Rating, Cafe
from datetime import datetime


def write_rating(db: Session, slack_user_id: str, place_id: str, score: int, comment: str = None):
    """
    Save a rating and update the parent cafe's running average.
    Creates the Cafe row if it doesn't exist yet (place_id is enough to identify it).
    """
    if not 1 <= score <= 5:
        raise ValueError("Score must be between 1 and 5")

    # Save rating row
    db.add(Rating(
        slack_user_id=slack_user_id,
        place_id=place_id,
        score=score,
        comment=comment,
        rated_at=datetime.utcnow()
    ))
    db.flush()

    # Recompute running average on the Cafe row
    cafe = db.query(Cafe).filter_by(place_id=place_id).first()
    if not cafe:
        raise ValueError(f"Cafe {place_id} not found in DB. Run /meetup-find first.")

    all_ratings = db.query(Rating).filter_by(place_id=place_id).all()
    scores = [r.score for r in all_ratings]
    cafe.total_votes         = len(scores)
    cafe.avg_internal_rating = round(sum(scores) / len(scores), 2)

    db.commit()
    return cafe


def get_ratings_map(db: Session, min_votes: int = 3) -> dict:
    """
    Returns {place_id: avg_internal_rating} for all cafes with >= min_votes ratings.
    Used by the minimax engine to blend ratings into the score.
    """
    cafes = db.query(Cafe).filter(Cafe.total_votes >= min_votes).all()
    return {c.place_id: c.avg_internal_rating for c in cafes if c.avg_internal_rating is not None}
```

---

## Step 2 — Cache cafes during `/meetup-find` (`app/cafes.py` update)

When `/meetup-find` runs and discovers cafes from Google Places, those cafes need to be saved to the DB so ratings can reference them later. Add this function to `cafes.py`:

```python
def upsert_cafes_to_db(db, cafe_list: list[dict]):
    """
    Save discovered cafes to the DB (insert if new, skip if already exists).
    Called automatically at the end of /meetup-find so /meetup-rate can reference them.
    """
    from .database import Cafe
    for c in cafe_list:
        existing = db.query(Cafe).filter_by(place_id=c["place_id"]).first()
        if not existing:
            db.add(Cafe(
                place_id      = c["place_id"],
                name          = c["name"],
                address       = c["address"],
                lat           = c["lat"],
                lng           = c["lng"],
                google_rating = c.get("google_rating"),
                total_votes   = 0
            ))
    db.commit()
```

Then in `slack_handlers.py`, call `upsert_cafes_to_db(db, cafes)` right after `find_nearby_cafes()` returns — before the minimax step.

---

## Step 3 — `/meetup-rate` slash command

Add this handler to `slack_handlers.py`:

```python
# ── /meetup-rate ──────────────────────────────────────────────────────────────

@app.command("/meetup-rate")
def handle_meetup_rate(ack, body, client):
    ack()

    # Build a dropdown of known cafes from the DB
    db: Session = SessionLocal()
    try:
        cafes = db.query(Cafe).all()
    finally:
        db.close()

    if not cafes:
        client.chat_postMessage(
            channel=body["user_id"],
            text="No cafes to rate yet. Run `/meetup-find` first to discover cafes."
        )
        return

    cafe_options = [
        {
            "text":  {"type": "plain_text", "text": c.name},
            "value": c.place_id
        }
        for c in cafes
    ]

    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type":        "modal",
            "callback_id": "rating_modal",
            "title":       {"type": "plain_text", "text": "Rate a cafe"},
            "submit":      {"type": "plain_text", "text": "Submit"},
            "blocks": [
                {
                    "type":     "input",
                    "block_id": "cafe_block",
                    "element": {
                        "type":        "static_select",
                        "action_id":   "cafe_select",
                        "placeholder": {"type": "plain_text", "text": "Pick a cafe"},
                        "options":     cafe_options
                    },
                    "label": {"type": "plain_text", "text": "Which cafe?"}
                },
                {
                    "type":     "input",
                    "block_id": "score_block",
                    "element": {
                        "type":      "static_select",
                        "action_id": "score_select",
                        "options": [
                            {"text": {"type": "plain_text", "text": "⭐ 1 — Poor"},      "value": "1"},
                            {"text": {"type": "plain_text", "text": "⭐⭐ 2 — Below average"}, "value": "2"},
                            {"text": {"type": "plain_text", "text": "⭐⭐⭐ 3 — OK"},        "value": "3"},
                            {"text": {"type": "plain_text", "text": "⭐⭐⭐⭐ 4 — Good"},     "value": "4"},
                            {"text": {"type": "plain_text", "text": "⭐⭐⭐⭐⭐ 5 — Excellent"}, "value": "5"},
                        ]
                    },
                    "label": {"type": "plain_text", "text": "Your rating"}
                },
                {
                    "type":     "input",
                    "block_id": "comment_block",
                    "optional": True,
                    "element": {
                        "type":        "plain_text_input",
                        "action_id":   "comment_input",
                        "multiline":   True,
                        "placeholder": {"type": "plain_text", "text": "Good wifi? Quiet enough? Parking?"}
                    },
                    "label": {"type": "plain_text", "text": "Comment (optional)"}
                }
            ]
        }
    )


@app.view("rating_modal")
def handle_rating_submission(ack, body, client):
    ack()
    values   = body["view"]["state"]["values"]
    place_id = values["cafe_block"]["cafe_select"]["selected_option"]["value"]
    score    = int(values["score_block"]["score_select"]["selected_option"]["value"])
    comment  = values["comment_block"]["comment_input"]["value"]
    user_id  = body["user"]["id"]

    db: Session = SessionLocal()
    try:
        from .ratings import write_rating
        cafe = write_rating(db, user_id, place_id, score, comment)
        client.chat_postMessage(
            channel=user_id,
            text=(
                f"Rating saved! *{cafe.name}* now has "
                f"{cafe.total_votes} internal vote(s) — "
                f"avg {cafe.avg_internal_rating}/5 ⭐"
            )
        )
    except Exception as e:
        client.chat_postMessage(channel=user_id, text=f"Couldn't save rating: {e}")
    finally:
        db.close()
```

Also add `/meetup-rate` as a new Slash Command in the Slack App settings pointing to the same `/slack/events` URL.

---

## Step 4 — Improve the `/meetup-find` Block Kit response

Replace the plain text blocks in the `/meetup-find` handler with this richer builder function. Add it above the command handlers in `slack_handlers.py`:

```python
def build_cafe_block(cafe: dict, rank: int) -> list:
    """
    Returns a list of Slack Block Kit blocks for one cafe card.
    rank is 0-indexed (0 = top pick).
    """
    medals  = ["🥇 Top pick", "🥈 Second choice", "🥉 Third choice"]
    heading = medals[rank] if rank < 3 else f"#{rank+1}"

    g_rating = f"⭐ {cafe['google_rating']}/5 on Google" if cafe.get("google_rating") else "No Google rating"
    i_rating = (
        f"  ·  🏠 *{cafe['internal_rating']:.1f}/5* internal ({cafe.get('total_votes',0)} votes)"
        if cafe.get("internal_rating") else ""
    )
    max_min  = cafe.get("max_travel_minutes", "?")
    avg_min  = round(cafe.get("avg_travel_seconds", 0) / 60)

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{heading} — {cafe['name']}*\n"
                    f"📍 {cafe['address']}\n"
                    f"{g_rating}{i_rating}\n"
                    f"🕐 Worst commute: *{max_min} min*  ·  avg {avg_min} min"
                )
            },
            "accessory": {
                "type":      "button",
                "text":      {"type": "plain_text", "text": "Rate this cafe"},
                "action_id": f"rate_cafe_{cafe['place_id']}",
                "value":     cafe["place_id"]
            }
        },
        {"type": "divider"}
    ]
```

Then replace the blocks loop in `/meetup-find` with:
```python
for i, cafe in enumerate(ranked):
    blocks.extend(build_cafe_block(cafe, i))
```

---

## Step 5 — Error handling pass

Add these guards throughout `slack_handlers.py` so the bot never goes silent on an error:

```python
# Wrap every DB call in try/finally (already shown above — double-check each handler)

# Add a global Slack error handler at the bottom of slack_handlers.py:
@app.error
def handle_errors(error, body, logger):
    logger.exception(f"Slack Bolt error: {error}")
    # Optionally notify a channel:
    # slack_app.client.chat_postMessage(channel="#bot-errors", text=str(error))
```

Edge cases to handle explicitly:
- `/meetup-find` with fewer than 2 employees → friendly message, don't call APIs
- Geocoding returns no results → DM the user with a suggestion to try a different input
- Distance Matrix returns `ZERO_RESULTS` for some employees → skip them, still return results
- `/meetup-rate` before any `/meetup-find` has run → "No cafes in the system yet"

---

## Step 6 — Deploy to Railway (free tier)

Railway gives you a free PostgreSQL database and a free web service. No credit card needed for the starter tier.

**One-time setup:**

1. Push your project to a GitHub repo (make sure `.env` is in `.gitignore`)
2. Go to https://railway.app → New Project → Deploy from GitHub repo
3. Railway detects Python automatically. Set the start command:
   ```
   uvicorn app.main:api --host 0.0.0.0 --port $PORT
   ```
4. Add a **PostgreSQL** plugin inside the same Railway project → Railway auto-sets `DATABASE_URL` as an environment variable
5. Add all other env vars under **Variables**:
   ```
   SLACK_BOT_TOKEN
   SLACK_SIGNING_SECRET
   GOOGLE_MAPS_API_KEY
   ```
6. Railway gives you a permanent URL like `https://attend-meetup-bot.up.railway.app`

**Update Slack App settings:**
- Replace the ngrok URL everywhere with your Railway URL
- Interactivity: `https://your-app.up.railway.app/slack/events`
- All three slash commands: same URL

**Run DB migrations on Railway:**
```bash
railway run python -c "from app.database import create_tables; create_tables()"
```

---

## Step 7 — Final test checklist

Run through this full flow on the deployed Railway URL:

- [ ] `/meetup-location` → save a zip code → confirm "Location saved as [neighborhood]"
- [ ] `/meetup-location` from a second account → save a different zip
- [ ] `/meetup-status` → both employees appear
- [ ] `/meetup-find` → 3 cafes returned with real travel times
- [ ] `/meetup-rate` → rate one cafe with a comment → confirm average updates
- [ ] `/meetup-find` again → the rated cafe shows internal rating in the card
- [ ] `/meetup-find` after 3+ ratings on one cafe → that cafe gets a rating boost in ranking

---

## Step 8 — README.md (write this last)

Your README is what the Attend team (or a hiring manager) reads. Keep it sharp:

```markdown
# MeetupBot — Equidistant Cafe Finder for Remote Teams

A Slack bot that finds the most fair-commute cafe for a distributed team to meet at,
using Google Maps Distance Matrix API and a minimax travel-time algorithm.

## How it works
1. Each employee runs `/meetup-location` once to save their home zip code or address
2. Anyone runs `/meetup-find` — the bot computes the centroid of all locations,
   fetches nearby cafes via Google Places, then ranks them by minimizing the
   *maximum* travel time any one person has to endure (minimax fairness)
3. After the meetup, everyone rates the cafe with `/meetup-rate` — ratings feed
   back into future recommendations (cafes with 3+ votes get a 20% score boost)

## Commands
| Command | What it does |
|---|---|
| `/meetup-location` | Save or update your home location (zip or address) |
| `/meetup-find` | Find the 3 most equidistant cafes for the team |
| `/meetup-rate` | Rate a cafe after a meetup |
| `/meetup-status` | See who has a saved location |

## Stack
- **Backend:** Python, FastAPI, Slack Bolt
- **Routing:** Google Maps Distance Matrix API (minimax algorithm)
- **Cafe discovery:** Google Places API
- **Database:** PostgreSQL (SQLAlchemy)
- **Deployment:** Railway

## Local setup
1. Clone the repo
2. Copy `.env.example` to `.env` and fill in keys
3. `pip install -r requirements.txt`
4. `createdb meetupbot`
5. `python -c "from app.database import create_tables; create_tables()"`
6. `uvicorn app.main:api --reload --port 3000`
7. Run ngrok and point Slack app to the ngrok URL
```

---

## Phase 2 done when:
- [ ] `/meetup-rate` works and updates the cafe's average
- [ ] `/meetup-find` shows internal ratings in the card when they exist
- [ ] Rated cafes visibly rank higher in future `/meetup-find` results (test with 3+ ratings)
- [ ] Bot is live on Railway — works without your laptop
- [ ] README is written and the GitHub repo is public

**Ship it. You now have a real, deployed product you built to solve a real problem for a real team. That's the story.**
