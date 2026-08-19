"""
Structured logging setup for the Locus service.
Configures a JSON log formatter so query volume, latency, and
match_type distribution (Section 10 of the design doc) can be shipped
to log aggregation and turned into dashboards without custom parsing.
"""


