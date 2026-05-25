"""Write and read cafe ratings.

write_rating()      – saves a rating and recomputes the cafe's running average.
get_ratings_map()   – {place_id: avg_internal_rating} for cafes with >= min_votes votes.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from .database import Cafe, Rating


def write_rating(
    db: Session,
    slack_user_id: str,
    place_id: str,
    score: int,
    comment: str | None = None,
) -> Cafe:
    """Save a rating row and update the parent cafe's running average.

    Raises ValueError if the score is out of range or the cafe is not in the DB
    (the cafe row is created by /meetup-find before any rating can target it).
    """
    if not 1 <= score <= 5:
        raise ValueError("Score must be between 1 and 5")

    db.add(
        Rating(
            slack_user_id=slack_user_id,
            place_id=place_id,
            score=score,
            comment=comment,
            rated_at=datetime.utcnow(),
        )
    )
    db.flush()

    cafe = db.query(Cafe).filter_by(place_id=place_id).first()
    if not cafe:
        raise ValueError(
            f"Cafe {place_id} not found in DB. Run /meetup-find first so the cafe is cached."
        )

    all_scores = [r.score for r in db.query(Rating).filter_by(place_id=place_id).all()]
    cafe.total_votes = len(all_scores)
    cafe.avg_internal_rating = round(sum(all_scores) / len(all_scores), 2)

    db.commit()
    return cafe


def get_ratings_map(db: Session, min_votes: int = 3) -> dict:
    """Return {place_id: avg_internal_rating} for cafes with >= min_votes ratings.

    Used by the minimax engine to blend internal ratings into the score.
    """
    cafes = db.query(Cafe).filter(Cafe.total_votes >= min_votes).all()
    return {
        c.place_id: c.avg_internal_rating
        for c in cafes
        if c.avg_internal_rating is not None
    }
