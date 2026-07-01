FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt pyproject.toml ./
COPY file_organizer ./file_organizer
COPY main.py ./

RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .

ENTRYPOINT ["file-organizer"]
CMD ["--help"]
