Lista diaria UN Journal
=======================

Guardá un archivo YYYY-MM-DD.txt en este directorio, un slug gadebate por línea,
en el orden de aparición del Journal. Las líneas que empiezan con # se ignoran.

Ejemplo (2026-09-22.txt):

secretary-general-united-nations
president-general-assembly-opening
brazil
kenya

Después (laptop → git, server extract):

  python -m pipeline roster --session 81 --day 2026-09-22
  python -m pipeline extract --session 81 --day 2026-09-22
  python -m pipeline publish --session 81 --day 2026-09-22 --github --sheet
