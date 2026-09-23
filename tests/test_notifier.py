from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.config import Config
from app.detector import DetectionEvent
from app.notifier import (
    EmailNotifier,
    NullNotifier,
    SmtpCredentials,
    active_smtp_credentials,
    build_email,
    build_notifier,
    mark_sent,
    select_events_for_alert,
)


def _event(keyword: str, context: str = '"...context..."') -> DetectionEvent:
    return DetectionEvent(
        timestamp=datetime(2026, 9, 1, 15, 32, 17, tzinfo=timezone.utc),
        keyword=keyword,
        transcript="Today we want to talk about women and gender.",
        context=context,
    )


class SelectEventsForAlertTest(unittest.TestCase):
    def test_keeps_first_match_per_keyword(self) -> None:
        events = [_event("women", '"first"'), _event("women", '"second"'), _event("gender")]
        selected = select_events_for_alert(events, {}, cooldown_seconds=120, now=10)
        self.assertEqual([e.keyword for e in selected], ["women", "gender"])
        self.assertEqual(selected[0].context, '"first"')

    def test_skips_keyword_still_in_cooldown(self) -> None:
        last_sent = {"women": 50.0}
        events = [_event("women"), _event("gender")]
        selected = select_events_for_alert(events, last_sent, cooldown_seconds=120, now=100)
        self.assertEqual([e.keyword for e in selected], ["gender"])

    def test_sends_again_after_cooldown(self) -> None:
        last_sent = {"women": 10.0}
        events = [_event("women")]
        selected = select_events_for_alert(events, last_sent, cooldown_seconds=120, now=140)
        self.assertEqual([e.keyword for e in selected], ["women"])

    def test_cooldown_zero_always_sends(self) -> None:
        last_sent = {"women": 100.0}
        selected = select_events_for_alert([_event("women")], last_sent, cooldown_seconds=0, now=100)
        self.assertEqual(len(selected), 1)


class BuildEmailTest(unittest.TestCase):
    def test_subject_and_body_include_keywords(self) -> None:
        subject, body, html = build_email(
            [_event("women", '"...talk about women..."'), _event("gender", '"...and gender..."')]
        )
        self.assertEqual(subject, "KEYWORD_DETECTED | women, gender")
        self.assertIn("Keyword(s): women, gender", body)
        self.assertIn("When: 2026-09-01 15:32:17 UTC", body)
        self.assertIn("New York: 2026-09-01 11:32:17 EDT", body)
        self.assertIn("Madrid: 2026-09-01 17:32:17 CEST", body)
        self.assertIn("Chunk:", body)
        self.assertIn("talk about **women** and **gender**", body)
        self.assertNotIn("embed", body)
        self.assertNotIn("context:", body)
        self.assertIn("<strong>women</strong>", html)
        self.assertIn("<strong>gender</strong>", html)

    def test_includes_speaker_live_url_without_timestamp(self) -> None:
        event = _event("women", '"...talk about women..."')
        event = DetectionEvent(
            timestamp=event.timestamp,
            keyword=event.keyword,
            transcript=event.transcript,
            context=event.context,
            speaker="Luiz Inacio Lula da Silva",
            speaker_title="President of Brazil",
            video_seconds=2845,
            watch_url="https://www.youtube.com/watch?v=KnIFmbdRCi0",
        )
        subject, body, html = build_email([event])
        self.assertEqual(subject, "KEYWORD_DETECTED | women | Luiz Inacio Lula da Silva")
        self.assertIn("Speaker: Luiz Inacio Lula da Silva", body)
        self.assertIn("YouTube: https://www.youtube.com/watch?v=KnIFmbdRCi0", body)
        self.assertNotIn("t=2845", body)
        self.assertNotIn("embed", body)
        self.assertNotIn("embed", html)
        self.assertNotIn("Player:", body)
        self.assertNotIn("UN Web TV:", body)
        self.assertIn("talk about **women** and gender", body)
        self.assertIn("<strong>women</strong>", html)

    def test_omits_player_and_webtv_link(self) -> None:
        event = DetectionEvent(
            timestamp=datetime(2026, 9, 1, 15, 32, 17, tzinfo=timezone.utc),
            keyword="women",
            transcript="Today we want to talk about women and gender.",
            context='"...talk about women..."',
            video_seconds=3600,
            watch_url="https://www.youtube.com/watch?v=KnIFmbdRCi0",
            webtv_url="https://webtv.un.org/en/asset/k10/k10h1p03zp?kalturaStartTime=3600",
            timestamp_reliable=False,
        )
        _subject, body, html = build_email([event])
        self.assertNotIn("Player:", body)
        self.assertNotIn("UN Web TV", body)
        self.assertNotIn("kalturaStartTime", body)
        self.assertNotIn("kalturaStartTime", html)
        self.assertIn("YouTube: https://www.youtube.com/watch?v=KnIFmbdRCi0", body)
        self.assertIn("Madrid:", body)

    def test_mail_context_replaces_chunk_label(self) -> None:
        event = DetectionEvent(
            timestamp=datetime(2026, 9, 1, 15, 32, 17, tzinfo=timezone.utc),
            keyword="women",
            transcript="talk about women",
            context='"women"',
            mail_context="Earlier the chair spoke. Then we talk about women and keep going.",
        )
        _subject, body, html = build_email([event])
        self.assertIn("Context:", body)
        self.assertNotIn("Chunk:", body)
        self.assertIn("Earlier the chair spoke. Then we talk about **women** and keep going.", body)
        self.assertIn("<strong>women</strong>", html)
        self.assertIn("Context", html)

    def test_mark_sent_uses_casefold(self) -> None:
        last_sent: dict[str, float] = {}
        mark_sent(last_sent, [_event("Women")], now=42.0)
        self.assertEqual(last_sent["women"], 42.0)


def _config(**overrides) -> Config:
    values = dict(
        stream_url="https://example.com",
        keywords=("women",),
        whisper_model="base",
        whisper_device="cpu",
        whisper_compute_type="int8",
        whisper_beam_size=1,
        whisper_vad=True,
        language="en",
        chunk_seconds=10,
        chunk_overlap_seconds=1,
        context_words=8,
        reconnect_delay=10,
        heartbeat_seconds=60,
        cpu_threads=4,
        log_level="INFO",
        log_dir="logs",
        cookies_file=None,
        audio_read_timeout=30,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="secret",
        smtp_from="bot@example.com",
        smtp_user_b="",
        smtp_password_b="",
        smtp_from_b="",
        alert_email_to=("alerts@example.com",),
        smtp_starttls=True,
        smtp_ssl=False,
        alert_cooldown_seconds=120,
        smtp_timeout=15,
    )
    values.update(overrides)
    return Config(**values)


class EmailNotifierTest(unittest.TestCase):
    def test_sends_one_email_for_chunk_and_respects_cooldown(self) -> None:
        sent: list[tuple[str, str]] = []
        clock = {"now": 10.0}

        class Recording(EmailNotifier):
            def _send(self, subject: str, body: str, creds: SmtpCredentials, html=None) -> None:
                sent.append((subject, body))

        notifier = Recording(_config(), clock=lambda: clock["now"])
        events = [_event("women"), _event("gender")]
        notifier.notify(events)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "KEYWORD_DETECTED | women, gender")
        self.assertEqual(notifier.emails_sent, 1)

        clock["now"] = 50.0
        notifier.notify(events)
        self.assertEqual(len(sent), 1)

        clock["now"] = 200.0
        notifier.notify(events)
        self.assertEqual(len(sent), 2)
        self.assertEqual(notifier.emails_sent, 2)

    def test_smtp_failure_does_not_arm_cooldown(self) -> None:
        clock = {"now": 10.0}

        class Failing(EmailNotifier):
            def _send(self, subject: str, body: str, creds: SmtpCredentials, html=None) -> None:
                raise OSError("smtp down")

        notifier = Failing(_config(), clock=lambda: clock["now"])
        notifier.notify([_event("women")])
        self.assertEqual(notifier._last_sent, {})
        self.assertEqual(notifier.emails_sent, 0)

    def test_build_notifier_disabled_without_smtp(self) -> None:
        notifier = build_notifier(_config(smtp_host="", smtp_from="", alert_email_to=()))
        self.assertIsInstance(notifier, NullNotifier)
        notifier.notify([_event("women")])
        self.assertFalse(notifier.notify_status("MONITOR_STALE", "body"))

    def test_cooldown_skip_logged_at_info(self) -> None:
        clock = {"now": 10.0}

        class Recording(EmailNotifier):
            def _send(self, subject: str, body: str, creds: SmtpCredentials, html=None) -> None:
                return None

        notifier = Recording(_config(), clock=lambda: clock["now"])
        notifier.notify([_event("women")])
        clock["now"] = 20.0
        with self.assertLogs("app.notifier", level="INFO") as captured:
            notifier.notify([_event("women")])
        self.assertTrue(any("EMAIL_SKIPPED_COOLDOWN" in line for line in captured.output))

    def test_active_smtp_credentials_rotates_by_hour(self) -> None:
        config = _config(
            smtp_password="key-a",
            smtp_password_b="key-b",
            smtp_user="resend",
            smtp_from="bot@alerts.example.com",
        )
        even = active_smtp_credentials(config, hour=14)
        odd = active_smtp_credentials(config, hour=15)
        self.assertEqual(even.slot, "A")
        self.assertEqual(even.password, "key-a")
        self.assertEqual(odd.slot, "B")
        self.assertEqual(odd.password, "key-b")

    def test_rotation_disabled_without_password_b(self) -> None:
        config = _config(smtp_password="only-a", smtp_password_b="")
        self.assertFalse(config.smtp_rotation_enabled)
        creds = active_smtp_credentials(config, hour=15)
        self.assertEqual(creds.slot, "A")
        self.assertEqual(creds.password, "only-a")

    def test_send_uses_active_slot_credentials(self) -> None:
        sent: list[SmtpCredentials] = []

        class Recording(EmailNotifier):
            def _send(self, subject: str, body: str, creds: SmtpCredentials, html=None) -> None:
                sent.append(creds)

        with patch(
            "app.notifier.active_smtp_credentials",
            return_value=SmtpCredentials(
                user="resend",
                password="key-b",
                from_addr="bot@alerts.example.com",
                slot="B",
            ),
        ):
            notifier = Recording(
                _config(smtp_password="key-a", smtp_password_b="key-b"),
                clock=lambda: 10.0,
            )
            notifier.notify([_event("women")])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].slot, "B")
        self.assertEqual(notifier.emails_sent, 1)

    def test_probe_tcp_reports_closed_port(self) -> None:
        from app.notifier import probe_tcp

        ok, detail = probe_tcp("127.0.0.1", 1, timeout=0.3)
        self.assertFalse(ok)
        self.assertTrue(detail)


class MailTestCliTest(unittest.TestCase):
    def test_probe_does_not_send_mail(self) -> None:
        from app import mail_test

        config = _config(smtp_host="smtp.mailgun.org", smtp_port=2525)
        with (
            patch("app.mail_test.Config.from_env", return_value=config),
            patch("app.mail_test.probe_tcp", return_value=(True, "220 ok")),
            patch("app.mail_test.send_smtp_email") as send,
        ):
            code = mail_test.main(["--probe"])
        self.assertEqual(code, 0)
        send.assert_not_called()

    def test_send_uses_smtp_not_http(self) -> None:
        from app import mail_test

        config = _config(
            smtp_host="smtp.mailgun.org",
            smtp_port=2525,
            smtp_from="bot@mails.example.com",
            alert_email_to=("alerts@example.com",),
        )
        with (
            patch("app.mail_test.Config.from_env", return_value=config),
            patch("app.mail_test.probe_tcp", return_value=(True, "220 ok")),
            patch("app.mail_test.send_smtp_email") as send,
        ):
            code = mail_test.main(["--to", "alerts@example.com"])
        self.assertEqual(code, 0)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["host"], "smtp.mailgun.org")
        self.assertEqual(kwargs["port"], 2525)
        self.assertTrue(kwargs["starttls"])
        self.assertFalse(kwargs["use_ssl"])


if __name__ == "__main__":
    unittest.main()
