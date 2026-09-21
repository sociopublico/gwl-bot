from __future__ import annotations

import logging
import smtplib
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
from app.youtube import format_timecode

logger = logging.getLogger(__name__)

_NY = ZoneInfo("America/New_York")

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


def _when_lines(stamp: datetime) -> tuple[str, str]:
    utc = stamp.astimezone(timezone.utc)
    ny = stamp.astimezone(_NY)
    return (
        f"{utc.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"{ny.strftime('%Y-%m-%d %H:%M:%S %Z')}",
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
    chunk = first.transcript
    marked_plain = mark_keywords_plain(chunk, keywords)
    marked_html = mark_keywords_html(chunk, keywords)
    when_utc, when_ny = _when_lines(first.timestamp)
    titles = list(dict.fromkeys(event.speaker_title for event in events if event.speaker_title))

    lines = [
        f"Keyword(s): {', '.join(keywords)}",
        f"When: {when_utc}",
        f"New York: {when_ny}",
    ]
    if first.video_seconds is not None:
        player = f"Player: {format_timecode(first.video_seconds)}"
        if first.webtv_url:
            lines.append(player)
        else:
            lines.append(
                f"{player} (YouTube live ignores timestamp links; use the bar if you seek)"
            )
    if first.webtv_url:
        lines.append(f"UN Web TV: {first.webtv_url}")
    if first.watch_url:
        lines.append(f"YouTube: {first.watch_url}")
    if speakers:
        lines.append(f"Speaker: {', '.join(speakers)}")
        if titles:
            lines.append(f"Title: {', '.join(titles)}")
    lines.append("")
    lines.append("Chunk:")
    lines.append(marked_plain)
    text = "\n".join(lines)

    meta_html = [
        f"<p><b>Keyword(s):</b> {html_escape(', '.join(keywords))}</p>",
        f"<p><b>When:</b> {html_escape(when_utc)}<br>"
        f"<b>New York:</b> {html_escape(when_ny)}</p>",
    ]
    if first.video_seconds is not None:
        player_html = (
            f"<p><b>Player:</b> {html_escape(format_timecode(first.video_seconds))}"
        )
        if first.webtv_url:
            meta_html.append(f"{player_html}</p>")
        else:
            meta_html.append(
                f"{player_html} (YouTube live ignores timestamp links)</p>"
            )
    if first.webtv_url:
        href = html_escape(first.webtv_url)
        meta_html.append(f'<p><b>UN Web TV:</b> <a href="{href}">{href}</a></p>')
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
        + "<p><b>Chunk:</b></p>"
        + '<blockquote style="margin:0;border-left:3px solid #222;padding:0.4em 0.8em">'
        + marked_html
        + "</blockquote></div>"
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
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = creds.from_addr
        message["To"] = ", ".join(self.config.alert_email_to)
        message.set_content(body)
        if html:
            message.add_alternative(html, subtype="html")

        if self.config.smtp_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=self.config.smtp_timeout,
                context=context,
            ) as client:
                self._login_and_send(client, message, creds)
            return

        with smtplib.SMTP(
            self.config.smtp_host,
            self.config.smtp_port,
            timeout=self.config.smtp_timeout,
        ) as client:
            if self.config.smtp_starttls:
                client.starttls(context=ssl.create_default_context())
            self._login_and_send(client, message, creds)

    def _login_and_send(
        self,
        client: smtplib.SMTP,
        message: EmailMessage,
        creds: SmtpCredentials,
    ) -> None:
        if creds.user:
            client.login(creds.user, creds.password)
        client.send_message(message)


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
