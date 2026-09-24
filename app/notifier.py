from __future__ import annotations

import logging
import smtplib
import socket
import ssl
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape as html_escape
from typing import Protocol
from zoneinfo import ZoneInfo

from app.config import Config
from app.detector import DetectionEvent, mark_keywords_html, mark_keywords_plain

logger = logging.getLogger(__name__)

_NY = ZoneInfo("America/New_York")
_MADRID = ZoneInfo("Europe/Madrid")

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    @property
    def emails_sent(self) -> int: ...

    def notify(self, events: Sequence[DetectionEvent]) -> None: ...

    def notify_status(self, subject: str, body: str) -> bool: ...


class NullNotifier:
    @property
    def emails_sent(self) -> int:
        return 0

    def notify(self, events: Sequence[DetectionEvent]) -> None:
        return None

    def send_speech(
        self,
        events: Sequence[DetectionEvent],
        *,
        name: str,
        country: str | None,
        title: str | None,
    ) -> bool:
        return False

    def notify_status(self, subject: str, body: str) -> bool:
        return False


@dataclass(frozen=True)
class SmtpCredentials:
    user: str
    password: str
    from_addr: str
    slot: str


def active_smtp_credentials(
    config: Config,
    *,
    hour: int | None = None,
) -> SmtpCredentials:
    """Hora par → slot A; hora impar → slot B (si SMTP_PASSWORD_B está seteado)."""
    if not config.smtp_rotation_enabled:
        return SmtpCredentials(
            user=config.smtp_user,
            password=config.smtp_password,
            from_addr=config.smtp_from,
            slot="A",
        )
    when = datetime.now().hour if hour is None else hour
    if when % 2 == 0:
        return SmtpCredentials(
            user=config.smtp_user,
            password=config.smtp_password,
            from_addr=config.smtp_from,
            slot="A",
        )
    return SmtpCredentials(
        user=config.smtp_user_b or config.smtp_user,
        password=config.smtp_password_b,
        from_addr=config.smtp_from_b or config.smtp_from,
        slot="B",
    )


def probe_tcp(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """Prueba si el host:puerto acepta TCP. No autentica SMTP."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            banner = b""
            try:
                banner = sock.recv(180)
            except TimeoutError:
                pass
            text = banner.decode("ascii", errors="replace").strip().splitlines()
            detail = text[0][:120] if text else "connected"
            return True, detail
    except OSError as exc:
        return False, str(exc)


def send_smtp_email(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    from_addr: str,
    to: Sequence[str],
    subject: str,
    text: str,
    html: str | None = None,
    starttls: bool = True,
    use_ssl: bool = False,
    timeout: float = 15,
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = ", ".join(to)
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    def _login_and_send(client: smtplib.SMTP) -> None:
        if user:
            client.login(user, password)
        client.send_message(message)

    ssl_mode = use_ssl or port == 465
    if ssl_mode:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as client:
            _login_and_send(client)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as client:
        if starttls:
            client.starttls(context=ssl.create_default_context())
        _login_and_send(client)


def select_events_for_alert(
    events: Sequence[DetectionEvent],
    last_sent: dict[str, float],
    cooldown_seconds: float,
    now: float,
) -> list[DetectionEvent]:
    """Una alerta por keyword (la primera del chunk) si pasó el cooldown."""
    selected: list[DetectionEvent] = []
    seen: set[str] = set()
    for event in events:
        key = event.keyword.casefold()
        if key in seen:
            continue
        previous = last_sent.get(key)
        if previous is not None and now - previous < cooldown_seconds:
            continue
        selected.append(event)
        seen.add(key)
    return selected


def mark_sent(last_sent: dict[str, float], events: Sequence[DetectionEvent], now: float) -> None:
    for event in events:
        last_sent[event.keyword.casefold()] = now


def _when_lines(stamp: datetime) -> tuple[str, str, str]:
    utc = stamp.astimezone(timezone.utc)
    ny = stamp.astimezone(_NY)
    madrid = stamp.astimezone(_MADRID)
    clock = "%Y-%m-%d %H:%M:%S %Z"
    return (
        f"{utc.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        ny.strftime(clock),
        madrid.strftime(clock),
    )


def build_email(events: Sequence[DetectionEvent]) -> tuple[str, str, str]:
    if not events:
        raise ValueError("no hay eventos para armar el email")

    keywords = list(dict.fromkeys(event.keyword for event in events))
    speakers = list(
        dict.fromkeys(event.speaker for event in events if event.speaker and event.speaker != "unknown")
    )
    subject = "KEYWORD_DETECTED | " + ", ".join(keywords)
    if speakers:
        subject += " | " + ", ".join(speakers)
    first = events[0]
    passage = first.mail_context or first.transcript
    passage_label = "Context" if first.mail_context else "Chunk"
    marked_plain = mark_keywords_plain(passage, keywords)
    marked_html = mark_keywords_html(passage, keywords)
    when_utc, when_ny, when_madrid = _when_lines(first.timestamp)
    titles = list(dict.fromkeys(event.speaker_title for event in events if event.speaker_title))

    lines = [
        f"Keyword(s): {', '.join(keywords)}",
        f"When: {when_utc}",
        f"New York: {when_ny}",
        f"Madrid: {when_madrid}",
    ]
    if first.watch_url:
        lines.append(f"YouTube: {first.watch_url}")
    if speakers:
        lines.append(f"Speaker: {', '.join(speakers)}")
        if titles:
            lines.append(f"Title: {', '.join(titles)}")
    lines.append("")
    lines.append(f"{passage_label}:")
    lines.append(marked_plain)
    text = "\n".join(lines)

    meta_html = [
        f"<p><b>Keyword(s):</b> {html_escape(', '.join(keywords))}</p>",
        f"<p><b>When:</b> {html_escape(when_utc)}<br>"
        f"<b>New York:</b> {html_escape(when_ny)}<br>"
        f"<b>Madrid:</b> {html_escape(when_madrid)}</p>",
    ]
    if first.watch_url:
        href = html_escape(first.watch_url)
        meta_html.append(f'<p><b>YouTube:</b> <a href="{href}">{href}</a></p>')
    if speakers:
        meta_html.append(f"<p><b>Speaker:</b> {html_escape(', '.join(speakers))}</p>")
        if titles:
            meta_html.append(f"<p><b>Title:</b> {html_escape(', '.join(titles))}</p>")
    html = (
        '<div style="font-family:sans-serif;max-width:40em;line-height:1.45">'
        + "".join(meta_html)
        + f"<p><b>{html_escape(passage_label)}:</b></p>"
        + '<blockquote style="margin:0;border-left:3px solid #222;padding:0.4em 0.8em">'
        + marked_html
        + "</blockquote></div>"
    )
    return subject, text, html


def _quote_order(event: DetectionEvent) -> tuple[datetime, float, str]:
    seconds = event.video_seconds if event.video_seconds is not None else 0.0
    return (event.timestamp, seconds, event.keyword.casefold())


def build_speech_email(
    events: Sequence[DetectionEvent],
    *,
    name: str,
    country: str | None,
    title: str | None,
) -> tuple[str, str, str]:
    """Un mail por discurso: asunto keywords | persona | país, citas en orden temporal."""
    if not events:
        raise ValueError("no hay eventos para armar el email")

    ordered = sorted(events, key=_quote_order)
    keywords = list(dict.fromkeys(event.keyword for event in ordered))
    person = (name or "").strip() or "unknown"
    country_name = (country or "").strip()
    title_name = (title or "").strip()
    subject_parts = [", ".join(keywords), person]
    if country_name:
        subject_parts.append(country_name)
    subject = " | ".join(subject_parts)

    lines: list[str] = [f"Speaker: {person}"]
    html_parts = [
        f"<p><b>Speaker:</b> {html_escape(person)}</p>",
    ]
    if country_name:
        lines.append(f"Country: {country_name}")
        html_parts.append(f"<p><b>Country:</b> {html_escape(country_name)}</p>")
    if title_name:
        lines.append(f"Title: {title_name}")
        html_parts.append(f"<p><b>Title:</b> {html_escape(title_name)}</p>")
    watch = next((event.watch_url for event in ordered if event.watch_url), None)
    if watch:
        lines.append(f"YouTube: {watch}")
        href = html_escape(watch)
        html_parts.append(f'<p><b>YouTube:</b> <a href="{href}">{href}</a></p>')
    lines.append("")

    for event in ordered:
        when_utc, when_ny, when_madrid = _when_lines(event.timestamp)
        stamp = f"{when_utc} | New York: {when_ny} | Madrid: {when_madrid} | {event.keyword}"
        quote = mark_keywords_plain(event.context, [event.keyword])
        lines.append(stamp)
        lines.append(quote)
        lines.append("")
        html_parts.append(
            f"<p><b>{html_escape(when_utc)}</b>"
            f" | New York: {html_escape(when_ny)}"
            f" | Madrid: {html_escape(when_madrid)}"
            f" | <b>{html_escape(event.keyword)}</b></p>"
            '<blockquote style="margin:0 0 1em;border-left:3px solid #222;padding:0.2em 0.8em">'
            + mark_keywords_html(event.context, [event.keyword])
            + "</blockquote>"
        )

    text = "\n".join(lines).rstrip() + "\n"
    html = (
        '<div style="font-family:sans-serif;max-width:40em;line-height:1.45">'
        + "".join(html_parts)
        + "</div>"
    )
    return subject, text, html


class EmailNotifier:
    def __init__(self, config: Config, clock=time.monotonic) -> None:
        self.config = config
        self._clock = clock
        self._last_sent: dict[str, float] = {}
        self._last_status_sent: float | None = None
        self._emails_sent = 0

    @property
    def emails_sent(self) -> int:
        return self._emails_sent

    def notify(self, events: Sequence[DetectionEvent]) -> None:
        now = self._clock()
        selected = select_events_for_alert(
            events,
            self._last_sent,
            self.config.alert_cooldown_seconds,
            now,
        )
        if not selected:
            logger.info(
                "EMAIL_SKIPPED_COOLDOWN | keywords=%s",
                ", ".join(dict.fromkeys(e.keyword for e in events)),
            )
            return

        subject, body, html = build_email(selected)
        creds = active_smtp_credentials(self.config)
        try:
            self._send(subject, body, creds, html=html)
        except Exception as exc:
            logger.error("Email alert failed: %s", exc)
            return

        mark_sent(self._last_sent, selected, now)
        self._emails_sent += 1
        logger.info(
            "Email alert sent | to=%s | subject=%s | smtp_slot=%s",
            ", ".join(self.config.alert_email_to),
            subject,
            creds.slot,
        )

    def send_speech(
        self,
        events: Sequence[DetectionEvent],
        *,
        name: str,
        country: str | None,
        title: str | None,
    ) -> bool:
        """Un mail con todas las citas del discurso. No aplica el cooldown por keyword."""
        if not events:
            return False
        subject, body, html = build_speech_email(
            events, name=name, country=country, title=title
        )
        creds = active_smtp_credentials(self.config)
        try:
            self._send(subject, body, creds, html=html)
        except Exception as exc:
            logger.error("Email alert failed: %s", exc)
            return False
        self._emails_sent += 1
        logger.info(
            "Email alert sent | to=%s | subject=%s | quotes=%s | smtp_slot=%s",
            ", ".join(self.config.alert_email_to),
            subject,
            len(events),
            creds.slot,
        )
        return True

    def notify_status(self, subject: str, body: str) -> bool:
        now = self._clock()
        previous = self._last_status_sent
        if (
            previous is not None
            and now - previous < self.config.watchdog_email_cooldown
        ):
            logger.info("MONITOR_STALE email skipped (cooldown)")
            return False

        creds = active_smtp_credentials(self.config)
        try:
            self._send(subject, body, creds)
        except Exception as exc:
            logger.error("Email alert failed: %s", exc)
            return False

        self._last_status_sent = now
        self._emails_sent += 1
        logger.info(
            "Email alert sent | to=%s | subject=%s | smtp_slot=%s",
            ", ".join(self.config.alert_email_to),
            subject,
            creds.slot,
        )
        return True

    def _send(
        self,
        subject: str,
        body: str,
        creds: SmtpCredentials,
        html: str | None = None,
    ) -> None:
        send_smtp_email(
            host=self.config.smtp_host,
            port=self.config.smtp_port,
            user=creds.user,
            password=creds.password,
            from_addr=creds.from_addr,
            to=self.config.alert_email_to,
            subject=subject,
            text=body,
            html=html,
            starttls=self.config.smtp_starttls,
            use_ssl=self.config.smtp_ssl,
            timeout=self.config.smtp_timeout,
        )


def build_notifier(config: Config) -> Notifier:
    if not config.email_enabled:
        if config.smtp_host or config.smtp_from or config.alert_email_to:
            logger.warning(
                "Email alerts disabled: set SMTP_HOST, SMTP_FROM and ALERT_EMAIL_TO together"
            )
        else:
            logger.info("Email alerts disabled (SMTP not configured)")
        return NullNotifier()

    rotation = "on (even=A odd=B)" if config.smtp_rotation_enabled else "off"
    logger.info(
        "Email alerts enabled | host=%s:%s | to=%s | cooldown=%.0fs | key_rotation=%s",
        config.smtp_host,
        config.smtp_port,
        ", ".join(config.alert_email_to),
        config.alert_cooldown_seconds,
        rotation,
    )
    return EmailNotifier(config)
