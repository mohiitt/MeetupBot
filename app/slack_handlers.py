"""Slack slash command handlers for the meetup bot."""

import os

from dotenv import load_dotenv
from slack_bolt import App
from sqlalchemy.orm import Session

from .cafes import find_nearby_cafes, upsert_cafes_to_db
from .database import Cafe as CafeModel
from .database import Employee, SessionLocal
from .location import compute_centroid, geocode_input
from .ratings import get_ratings_map, write_rating
from .routing import rank_cafes_minimax

load_dotenv()

app = App(
    token=os.getenv("SLACK_BOT_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
    token_verification_enabled=False,
)


def google_maps_url(place_id: str) -> str:
    """Google Maps deep link for an exact Google Places result."""
    return f"https://www.google.com/maps/search/?api=1&query=Google&query_place_id={place_id}"


def truncate_option_text(text: str, max_length: int = 75) -> str:
    """Slack static select option labels cannot exceed 75 chars."""
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def build_rating_modal(cafes: list[CafeModel], initial_place_id: str | None = None) -> dict:
    """Build the /meetup-rate modal, optionally preselecting a cafe."""
    cafe_options = [
        {
            "text": {"type": "plain_text", "text": truncate_option_text(c.name)},
            "value": c.place_id,
        }
        for c in cafes[:100]
    ]
    initial_option = next(
        (option for option in cafe_options if option["value"] == initial_place_id),
        None,
    )

    cafe_select = {
        "type": "static_select",
        "action_id": "cafe_select",
        "placeholder": {"type": "plain_text", "text": "Pick a cafe"},
        "options": cafe_options,
    }
    if initial_option:
        cafe_select["initial_option"] = initial_option

    return {
        "type": "modal",
        "callback_id": "rating_modal",
        "title": {"type": "plain_text", "text": "Rate a cafe"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "blocks": [
            {
                "type": "input",
                "block_id": "cafe_block",
                "element": cafe_select,
                "label": {"type": "plain_text", "text": "Which cafe?"},
            },
            {
                "type": "input",
                "block_id": "score_block",
                "element": {
                    "type": "static_select",
                    "action_id": "score_select",
                    "options": [
                        {"text": {"type": "plain_text", "text": "1 - Poor"}, "value": "1"},
                        {
                            "text": {"type": "plain_text", "text": "2 - Below average"},
                            "value": "2",
                        },
                        {"text": {"type": "plain_text", "text": "3 - OK"}, "value": "3"},
                        {"text": {"type": "plain_text", "text": "4 - Good"}, "value": "4"},
                        {
                            "text": {"type": "plain_text", "text": "5 - Excellent"},
                            "value": "5",
                        },
                    ],
                },
                "label": {"type": "plain_text", "text": "Your rating"},
            },
            {
                "type": "input",
                "block_id": "comment_block",
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "comment_input",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Good wifi? Quiet enough? Parking?",
                    },
                },
                "label": {"type": "plain_text", "text": "Comment (optional)"},
            },
        ],
    }


def build_cafe_block(cafe: dict, rank: int) -> list:
    """Return Slack Block Kit blocks for one cafe recommendation."""
    headings = [
        ":1st_place_medal: Top pick",
        ":2nd_place_medal: Second choice",
        ":3rd_place_medal: Third choice",
        "#4",
        "#5",
    ]
    heading = headings[rank] if rank < len(headings) else f"#{rank + 1}"

    maps_url = google_maps_url(cafe["place_id"])
    g_rating = (
        f":star: {cafe['google_rating']}/5 on Google"
        if cafe.get("google_rating")
        else "No Google rating"
    )
    i_rating = (
        f"  |  :house: *{cafe['internal_rating']:.1f}/5* internal "
        f"({cafe.get('total_votes', 0)} votes)"
        if cafe.get("internal_rating")
        else ""
    )
    max_min = cafe.get("max_travel_minutes", "?")
    avg_min = round(cafe.get("avg_travel_seconds", 0) / 60)

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{heading} - <{maps_url}|{cafe['name']}>*\n"
                    f":round_pushpin: {cafe['address']}\n"
                    f"{g_rating}{i_rating}\n"
                    f":clock3: Worst commute: *{max_min} min*  |  avg {avg_min} min"
                ),
            },
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Rate this cafe"},
                "action_id": "rate_cafe",
                "value": cafe["place_id"],
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<{maps_url}|Open in Google Maps>",
                }
            ],
        },
        {"type": "divider"},
    ]


def open_rating_modal(client, trigger_id: str, initial_place_id: str | None = None) -> bool:
    """Open the rating modal. Returns False when no cafes exist yet."""
    db: Session = SessionLocal()
    try:
        cafes = db.query(CafeModel).order_by(CafeModel.name).all()
    finally:
        db.close()

    if not cafes:
        return False

    client.views_open(
        trigger_id=trigger_id,
        view=build_rating_modal(cafes, initial_place_id=initial_place_id),
    )
    return True


# -- /meetup-location ----------------------------------------------------------


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
    username = body["user"].get("username") or body["user"].get("name") or user_id

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


# -- /meetup-find --------------------------------------------------------------


@app.command("/meetup-find")
def handle_meetup_find(ack, body, respond):
    ack()
    respond("Finding the top 5 cafes for your team... give me a moment :coffee:")

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

        upsert_cafes_to_db(db, cafes)
        db_cafes = {c.place_id: c for c in db.query(CafeModel).all()}
        db_ratings = get_ratings_map(db, min_votes=3)

        try:
            ranked = rank_cafes_minimax(coords, cafes, db_ratings)
        except Exception as e:
            respond(f"Couldn't compute travel times: {e}")
            return

        for cafe in ranked:
            db_cafe = db_cafes.get(cafe["place_id"])
            if db_cafe:
                cafe["total_votes"] = db_cafe.total_votes
                cafe["internal_rating"] = db_cafe.avg_internal_rating
    finally:
        db.close()

    if not ranked:
        respond("Couldn't rank any cafes - Distance Matrix returned no results.")
        return

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":coffee: Top 5 meetup cafes"},
        }
    ]
    for i, cafe in enumerate(ranked):
        blocks.extend(build_cafe_block(cafe, i))

    respond(blocks=blocks)


# -- /meetup-rate --------------------------------------------------------------


@app.command("/meetup-rate")
def handle_meetup_rate(ack, body, client):
    ack()
    opened = open_rating_modal(client, body["trigger_id"])
    if not opened:
        client.chat_postMessage(
            channel=body["user_id"],
            text="No cafes to rate yet. Run `/meetup-find` first to discover cafes.",
        )


@app.action("rate_cafe")
def handle_rate_cafe_button(ack, body, client):
    ack()
    place_id = body["actions"][0]["value"]
    opened = open_rating_modal(client, body["trigger_id"], initial_place_id=place_id)
    if not opened:
        client.chat_postMessage(
            channel=body["user"]["id"],
            text="No cafes to rate yet. Run `/meetup-find` first to discover cafes.",
        )


@app.view("rating_modal")
def handle_rating_submission(ack, body, client):
    ack()
    values = body["view"]["state"]["values"]
    place_id = values["cafe_block"]["cafe_select"]["selected_option"]["value"]
    score = int(values["score_block"]["score_select"]["selected_option"]["value"])
    comment = values["comment_block"]["comment_input"].get("value")
    user_id = body["user"]["id"]

    db: Session = SessionLocal()
    try:
        cafe = write_rating(db, user_id, place_id, score, comment)
        client.chat_postMessage(
            channel=user_id,
            text=(
                f"Rating saved! *{cafe.name}* now has "
                f"{cafe.total_votes} internal vote(s) - "
                f"avg {cafe.avg_internal_rating}/5."
            ),
        )
    except Exception as e:
        client.chat_postMessage(channel=user_id, text=f"Couldn't save rating: {e}")
    finally:
        db.close()


# -- /meetup-status ------------------------------------------------------------


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
        lines.append(f"- @{e.display_name} - {e.neighborhood}")

    respond("\n".join(lines))


@app.error
def handle_errors(error, body, logger):
    logger.exception(f"Slack Bolt error: {error}")
