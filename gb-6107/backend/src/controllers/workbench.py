from fastapi import HTTPException
from redis.exceptions import RedisError
from src.config.resources import cache, engine
from src.domain import scenario
from src.services import cases, readings, reviews, runs


def new_case(data, user):
    with engine.begin() as db:
        return cases.create(db, data, user["username"])


def new_run(case_id, data, user):
    with engine.begin() as db:
        run = runs.enqueue(db, case_id, data.parameter, user["username"])
    try:
        cache.lpush("workbench:wake", str(run["id"]))
        cache.ltrim("workbench:wake", 0, 99)
    except RedisError:
        # The database holds the durable queue. The worker also polls it.
        pass
    return run


def new_review(run_id, data, user):
    with engine.begin() as db:
        return reviews.sign(db, run_id, data, user["username"])


def correct_readings(case_id, data, user):
    try:
        updates, note = scenario.normalize_correction(data.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with engine.begin() as db:
        return readings.submit_correction(
            db, case_id, updates, note, user["username"], data.simulate_abort
        )


def case_versions(case_id):
    with engine.connect() as db:
        cases.get(db, case_id)
        return readings.list_versions(db, case_id)
