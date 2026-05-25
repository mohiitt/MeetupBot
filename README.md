# MeetupBot - Equidistant Cafe Finder for Remote Teams

A Slack bot that finds fair cafe meetup spots for distributed teams using Google Maps and a minimax travel-time algorithm.

## How It Works

1. Each employee runs `/meetup-location` once to save their home zip code or address.
2. Anyone runs `/meetup-find` to fetch nearby cafes and rank them by minimizing the maximum commute any one person has to take.
3. The bot returns the top 5 cafes, each with a Google Maps link, Google rating, worst commute, and average commute.
4. After a meetup, employees run `/meetup-rate` or click "Rate this cafe" so internal ratings can improve future recommendations.

## Commands

- `/meetup-location` - Save or update your home location.
- `/meetup-find` - Find the top 5 minimax-fair cafes for the team.
- `/meetup-rate` - Rate a discovered cafe from 1 to 5.
- `/meetup-status` - See who has saved a location.

## Stack

- Python
- FastAPI
- Slack Bolt
- Google Maps Geocoding, Places, and Distance Matrix APIs
- PostgreSQL with SQLAlchemy
- Railway

## Local Setup

1. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in real values.
4. Create the local database:
   ```bash
   createdb meetupbot
   python -c "from app.database import create_tables; create_tables()"
   ```
5. Start the app:
   ```bash
   uvicorn app.main:api --reload --port 3000
   ```
6. Start ngrok and point Slack slash commands plus interactivity to:
   ```text
   https://your-ngrok-url.ngrok.io/slack/events
   ```

## Railway Deployment

1. Push this repo to GitHub.
2. Create a Railway project from the GitHub repo.
3. Add a PostgreSQL plugin.
4. Set variables in Railway:
   - `SLACK_BOT_TOKEN`
   - `SLACK_SIGNING_SECRET`
   - `GOOGLE_MAPS_API_KEY`
   - `DATABASE_URL` (provided by Railway Postgres)
5. Railway uses the `Procfile` start command:
   ```bash
   uvicorn app.main:api --host 0.0.0.0 --port $PORT
   ```
6. Create tables on Railway:
   ```bash
   railway run python -c "from app.database import create_tables; create_tables()"
   ```
7. Update Slack slash command and interactivity URLs to:
   ```text
   https://your-railway-app.up.railway.app/slack/events
   ```

## Done Checklist

- `/meetup-location` saves locations.
- `/meetup-status` lists saved employees.
- `/meetup-find` returns top 5 cafes with Google Maps links and real travel times.
- `/meetup-rate` saves ratings and updates cafe averages.
- Cafes with at least 3 internal ratings influence future rankings.
- Railway deployment works without a laptop running.
