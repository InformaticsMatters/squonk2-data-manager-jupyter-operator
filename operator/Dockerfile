FROM python:3.10.4-slim

# Labels
LABEL maintainer='Alan Christie <achristie@informaticsmatters.com>'

# Force the binary layer of the stdout and stderr streams
# to be unbuffered
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install -r requirements.txt

WORKDIR /src
COPY handlers.py /src/
COPY entrypoint.sh /src/

CMD ["./entrypoint.sh"]
