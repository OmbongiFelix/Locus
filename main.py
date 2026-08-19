"""
FastAPI application entrypoint for the Locus reverse-geocoding service.
Builds the FastAPI app, mounts the versioned router from api/routes.py,
and wires startup/shutdown events that open and close the database
connection pool and the Redis client.
This is the only file that should be run directly
(uvicorn locus.main:app). All request-handling logic lives in api/
and domain/ — this file is composition only.
"""



def main():
    print("Hello from locus!")


if __name__ == "__main__":
    main()
