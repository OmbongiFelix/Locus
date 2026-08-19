"""
CLI script: loads a raw shapefile/GeoJSON release into the staging
table.
Implements the ingestion steps from the design document (Section 2):
CRS standardization to EPSG:4326, topology cleaning, multipart
normalization (ST_Multi), and load into
kenya_admin_boundaries_staging. Run manually or via a scheduled job
when IEBC/KNBS/HDX publish a new boundary release.
Usage:
python -m locus.pipeline.ingest --source data/iebc_2024.shp \
--source-dataset IEBC --boundary-version iebc-2024-delimitation
"""

