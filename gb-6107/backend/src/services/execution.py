import logging
from time import time

from sqlalchemy import select, update

from src.config.resources import engine
from src.constants.job_state import JobState
from src.domain import scenario
from src.models.cases import cases
from src.models.runs import runs
from src.services import readings
from src.services.audit import record
from src.utils.serial import content_digest

logger = logging.getLogger(__name__)


def _execute(db, task):
    case_payload = db.scalar(select(cases.c.payload).where(cases.c.id == task["case_id"]))
    _records, predicate = scenario.normalize(case_payload)

    # 严格按入队时钉住的读数版本取页；版本已切到新版也不影响在途旧批次。
    version_id = task["reading_version_id"]
    version = readings.get_version(db, version_id)
    page_map = readings.load_version_pages(db, version_id)
    pages = [page_map[index] for index in sorted(page_map)]
    version_records = [(row["serial"], row["power_cw"]) for row in version["records"]]

    result = scenario.run_version(
        version_records, pages, predicate, version["version_no"]
    )
    result["reading_version"] = {
        "id": version["id"], "version_no": version["version_no"], "status": version["status"],
    }
    return result


def service_one():
    with engine.begin() as db:
        statement = (
            select(runs).where(runs.c.status == JobState.QUEUED).order_by(runs.c.id)
            .limit(1).with_for_update(skip_locked=True)
        )
        task = db.execute(statement).mappings().first()
        if task is None:
            return False
        try:
            result = _execute(db, task)
            output = {"status": JobState.DONE, "result": result, "digest": content_digest(result)}
        except Exception as exc:
            # Let the failed database transaction roll back before persisting the error.
            raise JobFailure(task["id"], exc) from exc
        finish(db, task["id"], output)
        return True


class JobFailure(Exception):
    def __init__(self, run_id, cause):
        super().__init__(str(cause))
        self.run_id = run_id
        self.cause = cause


def fail(job_failure):
    with engine.begin() as db:
        run_id = job_failure.run_id
        statement = select(runs.c.status).where(runs.c.id == run_id).with_for_update()
        if db.scalar(statement) != JobState.QUEUED:
            return
        cause = job_failure.cause
        if isinstance(cause, (ValueError, TypeError, KeyError, OverflowError)):
            reason = str(cause) or "输入资料或批次参数不符合要求"
        else:
            reason = "处理失败，请检查 worker 日志"
        finish(db, run_id, {"status": JobState.FAILED, "failure": reason[:320]})


def finish(db, run_id, output):
    db.execute(update(runs).where(runs.c.id == run_id).values(**output, finished_at=int(time())))
    record(db, str(output["status"]), "worker", "run", run_id)
