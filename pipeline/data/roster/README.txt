Rosters diarios (laptop → git)
==============================

El WAF de CloudFront bloquea el scrape de gadebate.un.org desde el VPS.
Estas fichas HTML se bajan en la laptop:

  pipeline/.venv/bin/python -m pipeline roster --session 80 --day 2025-09-23
  git add pipeline/data/roster/80/2025-09-23.json && git commit && git push

El server hace git pull y corre extract (no fetch contra gadebate).
