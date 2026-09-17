"""Database query layer.

Repositories are the *only* place that issues SQLAlchemy queries. API
routes never construct a query directly (see api/__init__.py); services
call repositories and apply business rules on the results. This keeps
routes thin, makes query logic independently testable, and means a future
change to how (say) "current ports for a host" is fetched touches exactly
one place.
"""
