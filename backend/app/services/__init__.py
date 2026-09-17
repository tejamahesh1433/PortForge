"""Business logic layer. Services call repositories and apply the rules
that make PortForge's central behavior correct (staleness rejection,
conflict evaluation, snapshot diffing, ...) -- API routes call services,
never repositories directly, and never embed a business rule inline.
"""
