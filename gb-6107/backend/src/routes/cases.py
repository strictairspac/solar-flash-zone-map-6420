from fastapi import APIRouter, Depends

from src.config.resources import engine
from src.controllers.access import identity, operator
from src.controllers.workbench import case_versions, correct_readings, new_case, new_run
from src.schemas.cases import CaseEntry, CaseInput
from src.schemas.readings import CorrectionInput, VersionEntry
from src.schemas.runs import RunEntry, RunInput
from src.services import cases

router = APIRouter(prefix="/cases", tags=["Cases"])


@router.get("", response_model=list[CaseEntry], dependencies=[Depends(identity)])
def list_cases():
    with engine.connect() as db:
        return cases.list_entries(db)


@router.get("/{case_id}", response_model=CaseEntry, dependencies=[Depends(identity)])
def case_detail(case_id: int):
    with engine.connect() as db:
        return cases.get(db, case_id)


@router.post("", response_model=CaseEntry, status_code=201)
def register(form: CaseInput, user=Depends(operator)):
    return new_case(form, user)


@router.post("/{case_id}/runs", response_model=RunEntry, status_code=202)
def schedule(case_id: int, form: RunInput, user=Depends(operator)):
    return new_run(case_id, form, user)


@router.get("/{case_id}/versions", response_model=list[VersionEntry],
            dependencies=[Depends(identity)])
def versions(case_id: int):
    return case_versions(case_id)


@router.post("/{case_id}/corrections", response_model=VersionEntry, status_code=201)
def correct(case_id: int, form: CorrectionInput, user=Depends(operator)):
    # 同一质保批按板件编号补录复测功率；只重建受影响页，齐备后一次性切换。
    return correct_readings(case_id, form, user)
