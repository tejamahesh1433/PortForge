"""Pydantic request/response schemas -- deliberately separate from the
SQLAlchemy ORM models in `models/`. An API contract and a storage schema
are different concerns that happen to overlap today; keeping them as
distinct classes means either can change without forcing the other to.
"""
