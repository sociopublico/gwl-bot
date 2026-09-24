"""Manda a mano el mail de un discurso guardado en logs/speeches.json.

El monitor, con ALERT_BATCH_PER_SPEAKER=true, solo lo manda cuando cambia el orador.
Si el proceso se corta, las citas quedan en el archivo.

  docker compose exec monitor python -m app.speech_mail --list
  docker compose exec monitor python -m app.speech_mail --country libya
  docker compose exec monitor python -m app.speech_mail --name "Mohamed Younis Menfi"
"""

from __future__ import annotations

import argparse
import sys

from app.config import Config
from app.notifier import build_notifier
from app.speech_batch import SpeechSender
from app.speech_store import SpeechStore, StoredSpeech, quotes_with_context


def send_saved(
    store: SpeechStore,
    notifier: SpeechSender,
    *,
    name: str | None = None,
    country: str | None = None,
    before_seconds: float = 75.0,
    after_seconds: float = 30.0,
) -> str:
    """'ok', 'missing', 'ambiguous' o 'failed'. Si el envío falla, devuelve las citas al archivo."""
    status, speech = store.take_match(name=name, country=country)
    if status != "ok" or speech is None or not speech.quotes:
        return status if status != "ok" else "missing"
    sent = notifier.send_speech(
        quotes_with_context(
            speech,
            before_seconds=before_seconds,
            after_seconds=after_seconds,
        ),
        name=speech.name,
        country=speech.country,
        title=speech.title,
    )
    if not sent:
        store.put_back(speech)
        return "failed"
    return "ok"


def _format_speech(speech: StoredSpeech) -> str:
    slug = speech.slug or "-"
    country = speech.country or "-"
    return f"{speech.name} | {country} | {slug} | {len(speech.quotes)} quotes"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mandar un mail guardado por orador")
    parser.add_argument("--list", action="store_true", help="mostrar discursos sin mandar")
    parser.add_argument("--name", default="", help="nombre de la persona, o una parte")
    parser.add_argument(
        "--country",
        default="",
        help="país o su slug (libya, syrian-arab-republic, iran)",
    )
    args = parser.parse_args(argv)

    config = Config.from_env()
    store = SpeechStore(config.log_dir)
    if args.list or (not args.name and not args.country):
        speeches = [speech for speech in store.list_speeches() if speech.quotes]
        if not speeches:
            print("No hay discursos guardados.")
            return 0 if args.list else 2
        for speech in speeches:
            print(_format_speech(speech))
        if not args.name and not args.country:
            print("Indicá --name o --country para mandar uno.", file=sys.stderr)
            return 2
        return 0

    notifier = build_notifier(config)
    status = send_saved(
        store,
        notifier,
        name=args.name or None,
        country=args.country or None,
        before_seconds=config.alert_text_before_seconds,
        after_seconds=config.alert_text_after_seconds,
    )
    if status == "ok":
        print("Mail enviado.")
        return 0
    if status == "ambiguous":
        print("Hay más de un discurso. Usá --name.", file=sys.stderr)
        for speech in store.list_speeches():
            print(_format_speech(speech), file=sys.stderr)
        return 1
    if status == "failed":
        print("No se pudo mandar el mail. Las citas siguen en el archivo.", file=sys.stderr)
        return 1
    who = args.name or args.country
    print(f"No hay citas para {who!r}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
