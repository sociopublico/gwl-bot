SETUP

python3 -m venv pipeline/.venv

pipeline/.venv/bin/pip install -U pip

pipeline/.venv/bin/pip install -r pipeline/requirements.txt  



ANALISIS

pipeline/.venv/bin/python -m pipeline roster  --session 80 --day 2025-09-23 --speakers-txt speakers.txt   

docker compose up --build



ALERTAS 

pipeline/.venv/bin/python -m pipeline roster --session 80 --day 2025-09-23

pipeline/.venv/bin/python -m pipeline fetch --session 80 --day 2025-09-23 --skip-existing

pipeline/.venv/bin/python -m pipeline publish --session 80 --day 2025-09-23 --sheet  --github

pipeline/.venv/bin/python -m pipeline analyze --session 80 --day 2025-09-23   