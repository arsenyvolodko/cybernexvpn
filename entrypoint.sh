#!/bin/bash
set -e

mkdir -p ./data/backend/static
mkdir -p ./data/postgres

python3 manage.py migrate
python3 manage.py collectstatic --noinput

gunicorn nexvpn.wsgi:application --bind 0.0.0.0:8000
