"""People / roster routes (staff-facing).

Lets teachers, TPOs, and admins list students - optionally filtered to one
section - so they can build attendance rosters and gradebook views.

This fills the gap where students are linked to a section (`users.section_id`)
but previously only admins could enumerate users. Reads here are open to all
staff roles; assigning a student to a section stays admin-only (see admin.py).

Mounted under the `/people` prefix by `main.py`.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Section, User, UserRole
from pagination import Pagination, paginate, pagination_params
from app.schemas import UserOut
from app.core.security import require_roles

router = APIRouter(prefix="/people", tags=["people"])

# Rosters are staff work: teachers (attendance/gradebook), TPOs, and admins.
staff_only = require_roles(UserRole.teacher, UserRole.tpo, UserRole.admin)


@router.get(
    "/students",
    response_model=list[UserOut],
)
def list_students(
    response: Response,
    section_id: int | None = None,
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
    page: Pagination = Depends(pagination_params),
) -> list[User]:
    """List student accounts, optionally filtered to one section.

    Pass ?section_id=<id> to get just that section's roster (e.g. for marking
    attendance). Omit it to list every student.
    """
    stmt = select(User).where(
        User.role == UserRole.student,
        User.tenant_id == staff.tenant_id,
    )
    if section_id is not None:
        section = db.get(Section, section_id)
        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Section not found"
            )
        stmt = stmt.where(User.section_id == section_id)
    return list(paginate(db, response, stmt.order_by(User.full_name), page))
