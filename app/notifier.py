from __future__ import annotations

import logging
import smtplib
import ssl
import time
from collections.abc import Sequence
from email.message import EmailMessage
from typing import Protocol

from app.config import Config
from app.detector import DetectionEvent
from app.youtube import format_timecode

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def notify(self, events: Sequence[DetectionEvent]) -> None: ...


class NullNotifier:
    def notify(self, events: Sequence[DetectionEvent]) -> None:
        return None


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


def build_email(events: Sequence[DetectionEvent]) -> tuple[str, str]:
    if not events:
        raise ValueError("no hay eventos para armar el email")

    keywords = list(dict.fromkeys(event.keyword for event in events))
    speakers = list(
        dict.fromkeys(event.speaker for event in events if event.speaker and event.speaker != "unknown")
    )
    subject = "KEYWORD_DETECTED | " + ", ".join(keywords)
    if speakers:
        subject += " | " + ", ".join(speakers)
    stamp = events[0].timestamp.strftime("%Y-%m-%d %H:%M:%S")
    first = events[0]
    lines = [
        f"Keyword(s): {', '.join(keywords)}",
        f"Time: {stamp}",
    ]
    if first.video_seconds is not None:
        lines.append(f"Video time: {format_timecode(first.video_seconds)}")
    if first.watch_url:
        if first.timestamp_reliable:
            lines.append(f"Watch: {first.watch_url}")
        else:
            lines.append(
                "Watch (t= may not match the player; live start time was unknown): "
                f"{first.watch_url}"
            )
    if speakers:
        lines.append(f"Speaker: {', '.join(speakers)}")
        titles = list(dict.fromkeys(event.speaker_title for event in events if event.speaker_title))
        if titles:
            lines.append(f"Title: {', '.join(titles)}")
    lines.append("")
    for event in events:
        lines.append(event.keyword)
        if event.speaker and event.speaker != "unknown":
            lines.append(f"  speaker: {event.speaker}")
        if event.speaker_title:
            lines.append(f"  title: {event.speaker_title}")
        if event.watch_url:
            lines.append(f"  watch: {event.watch_url}")
        lines.append(f"  context: {event.context}")
        lines.append("")
    lines.append("Transcript:")
    lines.append(events[0].transcript)
    return subject, "\n".join(lines)


class EmailNotifier:
    def __init__(self, config: Config, clock=time.monotonic) -> None:
        self.config = config
        self._clock = clock
        self._last_sent: dict[str, float] = {}

    def notify(self, events: Sequence[DetectionEvent]) -> None:
        now = self._clock()
        selected = select_events_for_alert(
            events,
            self._last_sent,
            self.config.alert_cooldown_seconds,
            now,
        )
        if not selected:
            logger.debug("Email skipped (cooldown) | keywords=%s", [e.keyword for e in events])
            return

        subject, body = build_email(selected)
        try:
            self._send(subject, body)
        except Exception as exc:
            logger.error("Email alert failed: %s", exc)
            return

        mark_sent(self._last_sent, selected, now)
        logger.info("Email alert sent | to=%s | subject=%s", ", ".join(self.config.alert_email_to), subject)

    def _send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.smtp_from
        message["To"] = ", ".join(self.config.alert_email_to)
        message.set_content(body)

        if self.config.smtp_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=self.config.smtp_timeout,
                context=context,
            ) as client:
                self._login_and_send(client, message)
            return

        with smtplib.SMTP(
            self.config.smtp_host,
            self.config.smtp_port,
            timeout=self.config.smtp_timeout,
        ) as client:
            if self.config.smtp_starttls:
                client.starttls(context=ssl.create_default_context())
            self._login_and_send(client, message)

    def _login_and_send(self, client: smtplib.SMTP, message: EmailMessage) -> None:
        if self.config.smtp_user:
            client.login(self.config.smtp_user, self.config.smtp_password)
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

    logger.info(
        "Email alerts enabled | host=%s:%s | to=%s | cooldown=%.0fs",
        config.smtp_host,
        config.smtp_port,
        ", ".join(config.alert_email_to),
        config.alert_cooldown_seconds,
    )
    return EmailNotifier(config)
