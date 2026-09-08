"""Operational tooling that inspects the platform from the outside (readiness, doctor).

Nothing in here is imported by the application path — ``answer``, ``ingestion``
and ``surfaces`` never learn which deployment shape they run in.
"""
