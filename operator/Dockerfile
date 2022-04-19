FROM python:3.9.12-slim

COPY requirements.txt ./
RUN pip install -r requirements.txt

WORKDIR /src
COPY handlers.py /src/
COPY entrypoint.sh /src/

CMD ./entrypoint.sh
