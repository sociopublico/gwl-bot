SETUP

python3 -m venv pipeline/.venv

pipeline/.venv/bin/pip install -U pip

pipeline/.venv/bin/pip install -r pipeline/requirements.txt  

---

SERVER (ALERTAS) 

al principio de cada dia:

- volver a sacar cookies por las dudas:
  - yt-dlp --cookies-from-browser firefox --cookies youtube.cookies.txt --skip-download "[https://www.youtube.com/watch?v=bZ99XPDm1vk"](https://www.youtube.com/watch?v=KnIFmbdRCi0)
  - scp youtube.cookies.txt root@ubuntu-socio-new:~/traefik/gwl-bot/
- pipeline/.venv/bin/python -m pipeline roster  --session 80 --day 2025-09-23 --speakers-txt speakers.txt     
- git push etc etc  
- docker compose up --build

---

LOCAL (ANALISIS)

al final de cada día: 

- pipeline/.venv/bin/python -m pipeline roster --session 80 --day 2025-09-23
- pipeline/.venv/bin/python -m pipeline fetch --session 80 --day 2025-09-23 --skip-existing
- pipeline/.venv/bin/python -m pipeline publish --session 80 --day 2025-09-23 --sheet  --github
- pipeline/.venv/bin/python -m pipeline coding --session 80 --day 2025-09-23 
- pipeline/.venv/bin/python -m pipeline coding-sheet --session 80 --day 2025-09-23

