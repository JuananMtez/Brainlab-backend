from fastapi import APIRouter, Depends, HTTPException, Response
from ..schemas.researcher import ResearcherResponse, ResearcherPost, ResearcherPutPassword, ResearcherLogin
from ..config.database import get_db
from sqlalchemy.orm import Session
from app.services import researcher as researcher_service
from starlette.status import HTTP_204_NO_CONTENT

researcher_controller = APIRouter(
    prefix="/researcher",
    tags=["researchers"])


@researcher_controller.post("/", response_model=ResearcherResponse, responses={
    409: {"description": "Operation forbidden"},
    201: {"description": "Created"}})
async def create_researcher(researcher: ResearcherPost, db: Session = Depends(get_db)):
    r = researcher_service.create_researcher(db, researcher)
    if r is None:
        raise HTTPException(status_code=409, detail="Email or user already registered")
    return r


@researcher_controller.post("/login", response_model=ResearcherResponse)
async def login(researcher: ResearcherLogin, db: Session = Depends(get_db)):
    r = researcher_service.login(db, researcher)
    if r is None:
        raise HTTPException(status_code=404, detail="User not found")
    return r


@researcher_controller.get("/", response_model=list[ResearcherResponse])
async def get_all_researchers(db: Session = Depends(get_db)):
    return researcher_service.get_all_researcher(db)


@researcher_controller.get("/{researcher_id}", response_model=ResearcherResponse)
async def get_researcher_id(researcher_id: int, db: Session = Depends(get_db)):
    r = researcher_service.get_researcher_id(db, researcher_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Researcher not found")
    return r


@researcher_controller.patch("/{researcher_id}")
async def put_password_researcher(researcher_id: int, researcher_put: ResearcherPutPassword, db: Session = Depends(get_db)):
    if researcher_service.change_password(db, researcher_id, researcher_put) is None:
        raise HTTPException(status_code=404, detail="Researcher not found")
    return Response(status_code=HTTP_204_NO_CONTENT)