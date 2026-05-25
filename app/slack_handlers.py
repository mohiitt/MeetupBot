"""Slack slash command handlers for the meetup bot.

Registers:
  /meetup-location  – opens a modal so the user can save zip code or address
  /meetup-find      – computes the minimax-fair top 3 cafes for the team
  /meetup-status    – lists which teammates have a saved location
"""

import os

from dotenv import load_dotenv
from slack_bolt import App
from sqlalchemy.orm import Session

from .cafes import find_nearby_cafes
from .database import Cafe as CafeModel
from .database import Employee, SessionLocal
from .location import compute_centroid, geocode_input
from .routing import rank_cafes_minimax

load_dotenv()

app = App(
    token=os.getenv("SLACK_BOT_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
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
                        "placeholder": {"type": "plain_text", "text": "e.g. 94105"},
                    },
                    "label": {"type": "plain_text", "text": "Zip code"},
                },
                {
                    "type": "input",
                    "block_id": "address_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "address_input",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "e.g. 123 Main St, San Jose CA",
                        },
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Home address (overrides zip if filled)",
                    },
                },
            ],
        },
    )


@app.view("location_modal")
def handle_location_submission(ack, body, client):
    ack()
    values = body["view"]["state"]["values"]
    zip_code = values["zip_block"]["zip_input"]["value"]
    address = values["address_block"]["address_input"]["value"]
    user_id = body["user"]["id"]
    username = body["user"]["username"]

    if not zip_code and not address:
        client.chat_postMessage(
            channel=user_id,
            text="You need to fill in either a zip code or an address. Run `/meetup-location` again.",
        )
        return

    try:
        geo = geocode_input(zip_code=zip_code, address=address)
    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"Couldn't geocode that location: {e}. Try a different zip or address.",
        )
        return

    db: Session = SessionLocal()
    try:
        employee = db.query(Employee).filter_by(slack_user_id=user_id).first()
        if employee:
            employee.lat = geo["lat"]
            employee.lng = geo["lng"]
            employee.neighborhood = geo["neighborhood"]
            employee.zip_code = zip_code
            employee.address = address
            employee.display_name = username
        else:
            db.add(
                Employee(
                    slack_user_id=user_id,
                    display_name=username,
                    zip_code=zip_code,
                    address=address,
                    lat=geo["lat"],
                    lng=geo["lng"],
                    neighborhood=geo["neighborhood"],
                )
            )
        db.commit()
    finally:
        db.close()

    client.chat_postMessage(
        channel=user_id,
        text=(
            f"Got it! Location saved as *{geo['neighborhood']}*. "
            "You'll be included in the next `/meetup-find`."
        ),
    )


# ── /meetup-find ──────────────────────────────────────────────────────────────


@app.command("/meetup-find")
def handle_meetup_find(ack, body, respond):
    ack()
    respond("Finding the best cafes for your team... give me a moment :coffee:")

    db: Session = SessionLocal()
    try:
        employees = db.query(Employee).all()
        if len(employees) < 2:
            respond(
                "Need at least 2 employees with saved locations. "
                "Ask the team to run `/meetup-location` first."
            )
            return

        coords = [(e.lat, e.lng) for e in employees]
        centroid = compute_centroid(coords)

        try:
            cafes = find_nearby_cafes(centroid)
        except Exception as e:
            respond(f"Couldn't fetch nearby cafes: {e}")
            return

        if not cafes:
            respond("No cafes found near the team midpoint. Try again later.")
            return

        db_cafes = {c.place_id: c for c in db.query(CafeModel).all()}
        db_ratings = {
            pid: c.avg_internal_rating
            for pid, c in db_cafes.items()
            if c.total_votes >= 3 and c.avg_internal_rating is not None
        }

        try:
            ranked = rank_cafes_minimax(coords, cafes, db_ratings)
        except Exception as e:
            respond(f"Couldn't compute travel times: {e}")
            return
    finally:
        db.close()

    if not ranked:
        respond("Couldn't rank any cafes — Distance Matrix returned no results.")
        return

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":coffee: Top 3 meetup cafes for your team"},
        }
    ]

    medals = [":1st_place_medal:", ":2nd_place_medal:", ":3rd_place_medal:"]
    for i, cafe in enumerate(ranked):
        g_rating = (
            f":star: {cafe['google_rating']}" if cafe.get("google_rating") else "No Google rating"
        )
        i_rating = (
            f"  ·  :house: {cafe['internal_rating']:.1f}/5 internal"
            if cafe.get("internal_rating")
            else ""
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{medals[i]} *{cafe['name']}*\n"
                        f"{cafe['address']}\n"
                        f"{g_rating}{i_rating}\n"
                        f":clock3: Max commute: *{cafe['max_travel_minutes']} min*"
                    ),
                },
            }
        )
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
