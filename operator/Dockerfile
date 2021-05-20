FROM python:3.9.5-slim

COPY requirements.txt ./
RUN pip install -r requirements.txt

WORKDIR /src
COPY handlers.py /src/

CMD kopf run ./handlers.py --verbose --log-format full
