import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.auth.dependencies import AdminCsrfPrincipal, AdminPrincipal
from pawe_api.db.models import RulePackageRecord
from pawe_api.db.session import get_db_session
from pawe_api.rules.packages import PackageValidation, RulePackage, validate_package

router = APIRouter(prefix="/api/v1/rule-packages", tags=["rule-packages"])
Db = Annotated[AsyncSession, Depends(get_db_session)]


class PackageResponse(BaseModel):
    id: uuid.UUID
    version: str
    content_hash: str
    validation: PackageValidation
    created_at: datetime


def response(row: RulePackageRecord) -> PackageResponse:
    return PackageResponse(
        id=row.id,
        version=row.version,
        content_hash=row.content_hash,
        validation=PackageValidation.model_validate(row.validation),
        created_at=row.created_at,
    )


@router.get("")
async def list_packages(admin: AdminPrincipal, db: Db) -> list[PackageResponse]:
    del admin
    rows = await db.scalars(
        select(RulePackageRecord).order_by(RulePackageRecord.created_at.desc()).limit(100)
    )
    return [response(row) for row in rows]


@router.post("", response_model=PackageResponse)
async def upload_package(request: Request, admin: AdminCsrfPrincipal, db: Db) -> PackageResponse:
    # Stream limits also apply to chunked requests without Content-Length.
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > 768_000:
            raise HTTPException(413, "Rule package exceeds size limit")
    try:
        package = RulePackage.model_validate_json(payload)
    except ValidationError as exc:
        # Never echo uploaded rule text or raw validation input to logs/frontend.
        raise HTTPException(422, "Invalid rule package schema") from exc
    digest = package.content_hash()
    result = validate_package(package)
    statement = (
        insert(RulePackageRecord)
        .values(
            id=uuid.uuid4(),
            version=package.version,
            content_hash=digest,
            payload=package.model_dump(mode="json"),
            validation=result.model_dump(mode="json"),
            created_by_user_id=uuid.UUID(str(admin.user.id)),
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing()
    )
    await db.execute(statement)
    row = await db.scalar(
        select(RulePackageRecord).where(RulePackageRecord.version == package.version)
    )
    if row is None or row.content_hash != digest:
        await db.rollback()
        raise HTTPException(
            409, "Version already exists with different content; upload a new version"
        )
    await db.commit()
    return response(row)


@router.get("/{package_id}/download", response_model=RulePackage)
async def download_package(package_id: uuid.UUID, admin: AdminPrincipal, db: Db) -> RulePackage:
    del admin
    row = await db.get(RulePackageRecord, package_id)
    if row is None:
        raise HTTPException(404, "Rule package not found")
    return RulePackage.model_validate(row.payload)


@router.post("/{package_id}/validate", response_model=PackageValidation)
async def check_package(
    package_id: uuid.UUID, admin: AdminCsrfPrincipal, db: Db
) -> PackageValidation:
    del admin
    row = await db.get(RulePackageRecord, package_id)
    if row is None:
        raise HTTPException(404, "Rule package not found")
    # Return current validator output; keep original upload/validation evidence immutable.
    return validate_package(RulePackage.model_validate(row.payload))
