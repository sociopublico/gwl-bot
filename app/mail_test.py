"""Probar SMTP del mailer (sin HTTP API).

No manda el monitor: solo abre TCP / manda un mail de prueba.

Desde el host, con el compose ya corriendo (rebuild una vez para copiar este módulo):

  docker compose exec monitor python -m app.mail_test --probe
  docker compose exec monitor python -m app.mail_test
  docker compose exec monitor python -m app.mail_test --port 2525 --to vos@org.org

Sin tocar el contenedor del live (usa la misma .env):

  docker compose run --rm --no-deps --build monitor python -m app.mail_test --probe
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from app.config import Config
from app.notifier import active_smtp_credentials, probe_tcp, send_smtp_email

MAILGUN_SMTP_PORTS = (2525, 587, 465, 25)


def _parse_to(raw: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    extras = tuple(part.strip() for part in raw.split(",") if part.strip())
    return extras or fallback


def probe_smtp_ports(host: str, ports: tuple[int, ...], timeout: float) -> int:
    print(f"=== SMTP TCP probe {host} (timeout={timeout:.0f}s) ===")
    failures = 0
    for port in ports:
        ok, detail = probe_tcp(host, port, timeout=timeout)
        status = "OK  " if ok else "FAIL"
        print(f"  {status} {host}:{port}  {detail}")
        if not ok:
            failures += 1
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probar SMTP del gwl-bot")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="solo TCP a 2525/587/465/25; no autentica ni manda mail",
    )
    parser.add_argument("--host", default="", help="override SMTP_HOST")
    parser.add_argument("--port", type=int, default=0, help="override SMTP_PORT al mandar")
    parser.add_argument("--to", default="", help="override ALERT_EMAIL_TO (coma-separado)")
    args = parser.parse_args(argv)

    config = Config.from_env()
    host = args.host or config.smtp_host
    if not host:
        print("SMTP_HOST vacío: no hay servidor SMTP configurado.", file=sys.stderr)
        return 2

    timeout = min(max(config.smtp_timeout, 3.0), 8.0)
    ports = MAILGUN_SMTP_PORTS
    configured = args.port or config.smtp_port
    if configured not in ports:
        ports = (configured, *ports)

    probe_smtp_ports(host, ports, timeout)
    if args.probe:
        return 0

    to = _parse_to(args.to, config.alert_email_to)
    if not to:
        print("Falta --to o ALERT_EMAIL_TO.", file=sys.stderr)
        return 2
    if not config.smtp_from:
        print("Falta SMTP_FROM.", file=sys.stderr)
        return 2

    port = args.port or config.smtp_port
    use_ssl = config.smtp_ssl or port == 465
    starttls = False if use_ssl else config.smtp_starttls
    creds = active_smtp_credentials(config)
    subject = "gwl-bot mail_test SMTP"
    text = (
        f"Prueba SMTP {host}:{port} ssl={use_ssl} starttls={starttls} "
        f"at {datetime.now(timezone.utc).isoformat()}"
    )
    print(f"=== send {host}:{port} ssl={use_ssl} starttls={starttls} from={creds.from_addr} to={', '.join(to)} ===")
    try:
        send_smtp_email(
            host=host,
            port=port,
            user=creds.user,
            password=creds.password,
            from_addr=creds.from_addr,
            to=to,
            subject=subject,
            text=text,
            starttls=starttls,
            use_ssl=use_ssl,
            timeout=config.smtp_timeout,
        )
    except Exception as exc:
        print(f"FAIL send: {exc}", file=sys.stderr)
        return 1
    print("OK send queued/accepted by SMTP server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
