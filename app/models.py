from sqlalchemy import String, Integer, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))
    nodes: Mapped[list["DocumentNode"]] = relationship(back_populates="document_version", cascade="all, delete-orphan")

class DocumentNode(Base):
    __tablename__ = "document_nodes"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    heading: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[int] = mapped_column(Integer, default=0)
    node_type: Mapped[str] = mapped_column(String(30), default="paragraph")
    body_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    logical_key: Mapped[str] = mapped_column(String(500), index=True)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    document_version: Mapped[DocumentVersion] = relationship(back_populates="nodes")
    __table_args__ = (UniqueConstraint("version_id", "logical_key", name="uq_version_logical_key"),)

class Selection(Base):
    __tablename__ = "selections"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    node_ids_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))
