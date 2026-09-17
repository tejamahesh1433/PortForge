"""Shared schema building blocks: pagination envelope and strict base config."""
from __future__ import annotations

from typing import Generic, List, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ApiModel(BaseModel):
    """Base for all API schemas: builds from ORM attributes, rejects
    unknown fields on input (never silently ignore a typo'd/malicious
    extra field from an authenticated-but-not-necessarily-trusted agent).
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
    limit: int
    offset: int
