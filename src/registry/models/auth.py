import hashlib
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from registry.db.database import Base

# JSON type alias for scopes stored as comma-separated string in SQLite-friendly way;
# using SQLAlchemy JSON works with both SQLite and PostgreSQL.
from sqlalchemy import JSON


# ---------------------------------------------------------------------------
# SQLAlchemy ORM
# ---------------------------------------------------------------------------

class AuthTokenORM(Base):
    __tablename__ = "auth_tokens"

    token_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class TokenCreateRequest(BaseModel):
    scopes: list[str] = ["full"]
    description: str = ""
    expires_in_days: int | None = None


class TokenCreateResponse(BaseModel):
    # token is returned ONCE — store securely, cannot be retrieved again
    token: str
    token_id: str
    expires_at: int | None  # Unix timestamp or null


class TokenMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token_id: str
    scopes: list[str]
    description: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    is_revoked: bool


# ---------------------------------------------------------------------------
# Valid scopes
# ---------------------------------------------------------------------------

VALID_SCOPES = frozenset({
    "registry:read",
    "registry:write",
    "registry:admin",
    "calibration:write",
    "full",
})
