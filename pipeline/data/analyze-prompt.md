# TODO: pegar el prompt de análisis

Este archivo es un stub. Cuando tengas el prompt de coding y un ejemplo de
columnas (xls o encabezados de la pestaña Analysis), reemplazá este texto.

Hasta entonces `python -m pipeline analyze` sale sin llamar a Claude.
Para forzar una corrida de prueba: `ANALYZE_ALLOW_STUB=1`.

Respondé únicamente un objeto JSON cuyas claves coincidan con las columnas
de la pestaña Analysis del spreadsheet. No inventes claves extra.

Campos de identidad (el pipeline los pisa con metadatos del .txt):
slug, date_time, id_speech, country, speaker_name, ficha_url

Campos de análisis (placeholder hasta tener el xls de ejemplo):
summary — un párrafo en inglés con el eje del discurso
notes — observaciones cortas; vacío si no hay nada relevante
