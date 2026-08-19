"""
QA gate run against the staging table before every publish.
Implements the checks from the design document (Section 9): geometry
validity (ST_IsValid / ST_MakeValid), row-count sanity against the
source's known feature count, and a spot-check against the
ground-truth landmark list. A failing run must block publish.py.
"""
