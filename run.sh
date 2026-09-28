#/bin/sh

pipeline/.venv/bin/python -m pipeline roster --session 81 --day 2026-09-26 
pipeline/.venv/bin/python -m pipeline fetch --session 81 --day 2026-09-26 --skip-existing
pipeline/.venv/bin/python -m pipeline publish --session 81 --day 2026-09-26 --sheet  --github
pipeline/.venv/bin/python -m pipeline coding --session 81 --day 2026-09-26
pipeline/.venv/bin/python -m pipeline coding-sheet --session 81 --day 2026-09-26
