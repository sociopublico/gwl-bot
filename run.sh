#/bin/sh

pipeline/.venv/bin/python -m pipeline publish --session 81 --day 2026-09-24 --sheet  --github
pipeline/.venv/bin/python -m pipeline coding --session 81 --day 2026-09-24
pipeline/.venv/bin/python -m pipeline coding-sheet --session 81 --day 2026-09-24
