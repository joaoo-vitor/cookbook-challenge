# The web page of the cookbook pipeline, as a container: see "Run with Docker" in README.md.
# Only the web page runs this way; the stage commands (python -m cookbook.extract, ...) are run
# from a virtual environment as described under Setup.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# The project is installed as a package (not editable) with the web extra. The key is not in the
# image: docker-compose.yml passes it in from .env when the container starts.
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[web]"

# A normal user. /app/.cache is where the model answers are cached (a volume in docker-compose.yml).
RUN useradd --create-home app && mkdir -p /app/.cache && chown -R app /app/.cache
USER app

EXPOSE 8000

# 0.0.0.0 inside the container; docker-compose.yml publishes the port on 127.0.0.1 only.
CMD ["python", "-m", "cookbook.web", "--host", "0.0.0.0", "--no-browser"]
