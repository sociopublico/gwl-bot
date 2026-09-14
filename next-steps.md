LOCAL

pipeline/.venv/bin/python -m pipeline roster --session 80 --day 2025-09-23

git add pipeline/data/roster/80/2025-09-23.json && git commit && git push



SERVER

pipeline/.venv/bin/python -m pipeline fetch --session 80 --day 2025-09-23

pipeline/.venv/bin/python -m pipeline publish --session 80 --day 2025-09-23 --sheet  