from sqlalchemy.orm import Session
from app.crud import researcher as researcher_crud
from app.models import models
from app.schemas.researcher import ResearcherPost, ResearcherPutPassword, ResearcherLogin
from typing import Optional
import bcrypt


def get_researcher_id(db: Session, researcher_id: int) -> models.Researcher:
    return researcher_crud.find_by_id(db, researcher_id)


def get_all_researcher(db: Session):
    return researcher_crud.find_all(db)


def create_researcher(db: Session, researcher: ResearcherPost):

    if researcher_crud.exists_by_user_email(db, researcher.email, researcher.user):
        return None

    password = researcher.password.encode('utf-8')
    hashed = bcrypt.hashpw(password, bcrypt.gensalt(10))

    db_researcher = models.Researcher(name=researcher.name,
                                      surname=researcher.surname,
                                      email=researcher.email,
                                      user=researcher.user,
                                      password=hashed)

    return researcher_crud.save(db, db_researcher)


def login(db: Session, researcher: ResearcherLogin) -> Optional[models.Researcher]:
    r = researcher_crud.find_by_user(db, researcher.user)

    if r is None:
        return None

    if bcrypt.checkpw(researcher.password.encode('utf-8'), bytes(r.password, 'utf-8')) \
            and r.user == researcher.user:
        return r
    return None


def change_password(db: Session, researcher_id: int, researcher_put: ResearcherPutPassword):

    r = researcher_crud.find_by_id(db, researcher_id)
    if r is None:
        return None

    r.password = researcher_put.password
    researcher_crud.save_new_password(db, r)
    return True