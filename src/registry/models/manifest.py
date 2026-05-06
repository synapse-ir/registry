import hashlib
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from sqlalchemy import JSON, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from registry.db.database import Base


# ---------------------------------------------------------------------------
# SQLAlchemy ORM
# ---------------------------------------------------------------------------

class ManifestORM(Base):
    __tablename__ = "manifests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Core identity
    manifest_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Capability arrays — stored as JSON
    task_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    domains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    input_modalities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    output_modalities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Performance profile — stored as JSON object
    perf_profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Compliance
    compliance_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    data_residency: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Adapters — stored as JSON
    adapters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Operational metadata
    heartbeat_endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(256), nullable=False)
    license: Mapped[str] = mapped_column(String(64), nullable=False)
    successor_model_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Registry-managed fields
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_deprecated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class PerfProfile(BaseModel):
    p50_latency_ms: int
    p95_latency_ms: int
    max_throughput_rps: int | None = None
    cost_per_1k_tokens: float | None = None
    max_content_length: int | None = None


class PythonAdapter(BaseModel):
    package: str
    version: str
    class_: str | None = None

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("class_", mode="before")
    @classmethod
    def accept_class_key(cls, v: object) -> object:
        return v


class TypeScriptAdapter(BaseModel):
    package: str
    version: str
    export: str | None = None


class AdapterRefs(BaseModel):
    python: PythonAdapter | None = None
    typescript: TypeScriptAdapter | None = None


class ManifestCreate(BaseModel):
    manifest_version: str
    model_id: str
    display_name: str
    model_version: str
    description: str

    task_types: list[str]
    domains: list[str]
    input_modalities: list[str]
    output_modalities: list[str]

    perf_profile: PerfProfile

    compliance_tags: list[str]
    data_residency: list[str]

    adapters: AdapterRefs

    heartbeat_endpoint: str
    contact_email: str
    license: str
    deprecated: bool = False
    successor_model_id: str | None = None


class ManifestUpdate(BaseModel):
    display_name: str | None = None
    model_version: str | None = None
    description: str | None = None
    task_types: list[str] | None = None
    domains: list[str] | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    perf_profile: PerfProfile | None = None
    compliance_tags: list[str] | None = None
    data_residency: list[str] | None = None
    adapters: AdapterRefs | None = None
    heartbeat_endpoint: str | None = None
    contact_email: str | None = None
    license: str | None = None
    deprecated: bool | None = None
    successor_model_id: str | None = None


class ManifestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    manifest_version: str
    model_id: str
    display_name: str
    model_version: str
    description: str

    task_types: list[str]
    domains: list[str]
    input_modalities: list[str]
    output_modalities: list[str]

    perf_profile: dict
    compliance_tags: list[str]
    data_residency: list[str]
    adapters: dict

    heartbeat_endpoint: str
    contact_email: str
    license: str
    is_deprecated: bool
    successor_model_id: str | None

    owner_hash: str
    created_at: datetime
    updated_at: datetime
