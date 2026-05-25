"""FastAPI entry point that mounts the Slack Bolt app.

A single POST endpoint at /slack/events handles all Slack traffic
(slash commands, modal submissions, etc.).
"""

from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi import SlackRequestHandler

from .slack_handlers import app as slack_app

api = FastAPI(title="MeetupBot")
handler = SlackRequestHandler(slack_app)


@api.get("/")
def health() -> dict:
    """Simple health check so deploys (Railway etc.) can confirm the app is up."""
    return {"status": "ok", "service": "meetupbot"}


@api.post("/slack/events")
async def slack_events(req: Request):
    return await handler.handle(req)
