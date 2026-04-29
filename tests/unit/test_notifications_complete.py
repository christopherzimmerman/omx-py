"""Tests for the complete notification system port."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from omx.notifications.types import (
    DiscordBotNotificationConfig,
    DiscordNotificationConfig,
    DispatchResult,
    EventNotificationConfig,
    FullNotificationConfig,
    FullNotificationPayload,
    NotificationEvent,
    NotificationPlatform,
    NotificationsBlock,
    ReplyConfig,
    SlackNotificationConfig,
    TelegramNotificationConfig,
    VerbosityLevel,
    WebhookNotificationConfig,
)
from omx.notifications.config import (
    build_config_from_env,
    get_enabled_platforms,
    get_verbosity,
    is_event_allowed_by_verbosity,
    is_event_enabled,
    parse_mention_allowed_mentions,
    resolve_profile_config,
    should_include_tmux_tail,
    validate_mention,
    validate_slack_mention,
)
from omx.notifications.formatter import (
    format_ask_user_question,
    format_notification,
    format_session_end,
    format_session_idle,
    format_session_start,
    format_session_stop,
    parse_tmux_tail,
)
from omx.notifications.template_engine import (
    compute_template_variables,
    get_default_template,
    interpolate_template,
    validate_template,
)
from omx.notifications.hook_config import (
    merge_hook_config_into_notification_config,
    reset_hook_config_cache,
    resolve_event_template,
)
from omx.notifications.hook_config_types import (
    HookEventConfig,
    HookNotificationConfig,
    PlatformTemplateOverride,
)
from omx.notifications.lifecycle_dedupe import (
    create_lifecycle_broadcast_fingerprint,
    record_lifecycle_notification_sent,
    should_dedupe_lifecycle_notification,
    should_send_lifecycle_notification,
)
from omx.notifications.idle_cooldown import (
    record_idle_notification_sent,
    should_send_idle_notification,
)
from omx.notifications.dispatch_cooldown import (
    should_send_dispatch_notification,
)
from omx.notifications.session_registry import (
    SessionMapping,
    lookup_by_message_id,
    register_message,
    remove_session,
)
from omx.notifications.session_status import (
    is_discord_status_command,
)
from omx.notifications.reply_listener import (
    RateLimiter,
    format_reply_acknowledgement,
    redact_sensitive_tokens,
    sanitize_reply_input,
)
from omx.notifications.temp_contract import (
    NotifyTempContract,
    NotifyTempSource,
    get_temp_builtin_selectors,
    is_notify_temp_env_active,
    is_openclaw_selected_in_temp_contract,
    parse_notify_temp_contract_from_args,
    read_notify_temp_contract_from_env,
    serialize_notify_temp_contract,
)
from omx.notifications.tmux_detector import (
    analyze_pane_content,
    build_capture_pane_argv,
    build_send_pane_argvs,
)
from omx.notifications.tmux_notify import sanitize_tmux_alert_text
from omx.notifications.notifier import (
    _build_desktop_args,
)
from omx.notifications.dispatcher import (
    send_discord,
    send_discord_bot,
    send_slack,
    send_telegram,
    send_webhook,
)


class TestNotificationTypes(unittest.TestCase):
    """Test notification type definitions."""

    def test_notification_event_values(self):
        self.assertEqual(NotificationEvent.SESSION_START, "session-start")
        self.assertEqual(NotificationEvent.SESSION_END, "session-end")
        self.assertEqual(NotificationEvent.ASK_USER_QUESTION, "ask-user-question")

    def test_verbosity_level_values(self):
        self.assertEqual(VerbosityLevel.VERBOSE, "verbose")
        self.assertEqual(VerbosityLevel.MINIMAL, "minimal")
        self.assertEqual(VerbosityLevel.SESSION, "session")

    def test_platform_values(self):
        self.assertEqual(NotificationPlatform.DISCORD_BOT, "discord-bot")
        self.assertEqual(NotificationPlatform.TELEGRAM, "telegram")

    def test_full_payload_defaults(self):
        p = FullNotificationPayload(event="session-start", session_id="abc")
        self.assertEqual(p.event, "session-start")
        self.assertEqual(p.session_id, "abc")
        self.assertIsNone(p.tmux_session)
        self.assertIsNone(p.duration_ms)

    def test_dispatch_result(self):
        r = DispatchResult(event="session-end", results=[], any_success=False)
        self.assertFalse(r.any_success)

    def test_reply_config_defaults(self):
        rc = ReplyConfig()
        self.assertTrue(rc.enabled)
        self.assertEqual(rc.poll_interval_ms, 3000)
        self.assertEqual(rc.max_message_length, 500)


class TestValidation(unittest.TestCase):
    """Test mention validation functions."""

    def test_validate_mention_valid_user(self):
        self.assertEqual(
            validate_mention("<@12345678901234567>"), "<@12345678901234567>"
        )

    def test_validate_mention_valid_role(self):
        self.assertEqual(
            validate_mention("<@&12345678901234567>"), "<@&12345678901234567>"
        )

    def test_validate_mention_invalid(self):
        self.assertIsNone(validate_mention("hello"))
        self.assertIsNone(validate_mention(""))
        self.assertIsNone(validate_mention(None))

    def test_validate_slack_mention_user(self):
        self.assertEqual(validate_slack_mention("<@UABCDEFGH>"), "<@UABCDEFGH>")

    def test_validate_slack_mention_here(self):
        self.assertEqual(validate_slack_mention("<!here>"), "<!here>")

    def test_validate_slack_mention_invalid(self):
        self.assertIsNone(validate_slack_mention("@everyone"))

    def test_parse_mention_allowed_mentions_user(self):
        result = parse_mention_allowed_mentions("<@12345678901234567>")
        self.assertEqual(result, {"users": ["12345678901234567"]})

    def test_parse_mention_allowed_mentions_role(self):
        result = parse_mention_allowed_mentions("<@&12345678901234567>")
        self.assertEqual(result, {"roles": ["12345678901234567"]})

    def test_parse_mention_allowed_mentions_none(self):
        self.assertEqual(parse_mention_allowed_mentions(None), {})


class TestVerbosity(unittest.TestCase):
    """Test verbosity configuration."""

    def test_get_verbosity_default(self):
        config = FullNotificationConfig(enabled=True)
        self.assertEqual(get_verbosity(config), VerbosityLevel.SESSION)

    def test_get_verbosity_from_config(self):
        config = FullNotificationConfig(enabled=True, verbosity=VerbosityLevel.VERBOSE)
        self.assertEqual(get_verbosity(config), VerbosityLevel.VERBOSE)

    @patch.dict(os.environ, {"OMX_NOTIFY_VERBOSITY": "minimal"})
    def test_get_verbosity_env_override(self):
        config = FullNotificationConfig(enabled=True, verbosity=VerbosityLevel.VERBOSE)
        self.assertEqual(get_verbosity(config), VerbosityLevel.MINIMAL)

    def test_event_allowed_by_verbosity(self):
        self.assertTrue(
            is_event_allowed_by_verbosity(VerbosityLevel.MINIMAL, "session-start")
        )
        self.assertFalse(
            is_event_allowed_by_verbosity(VerbosityLevel.MINIMAL, "session-idle")
        )
        self.assertTrue(
            is_event_allowed_by_verbosity(VerbosityLevel.SESSION, "session-idle")
        )
        self.assertFalse(
            is_event_allowed_by_verbosity(VerbosityLevel.SESSION, "ask-user-question")
        )
        self.assertTrue(
            is_event_allowed_by_verbosity(VerbosityLevel.AGENT, "ask-user-question")
        )

    def test_should_include_tmux_tail(self):
        self.assertFalse(should_include_tmux_tail(VerbosityLevel.MINIMAL))
        self.assertTrue(should_include_tmux_tail(VerbosityLevel.SESSION))
        self.assertTrue(should_include_tmux_tail(VerbosityLevel.VERBOSE))


class TestIsEventEnabled(unittest.TestCase):
    """Test event enablement logic."""

    def test_disabled_config(self):
        config = FullNotificationConfig(enabled=False)
        self.assertFalse(is_event_enabled(config, "session-start"))

    def test_enabled_with_platform(self):
        config = FullNotificationConfig(
            enabled=True,
            discord=DiscordNotificationConfig(
                enabled=True, webhook_url="https://discord.com/test"
            ),
        )
        self.assertTrue(is_event_enabled(config, "session-start"))

    def test_event_explicitly_disabled(self):
        config = FullNotificationConfig(
            enabled=True,
            discord=DiscordNotificationConfig(
                enabled=True, webhook_url="https://discord.com/test"
            ),
            events={"session-start": EventNotificationConfig(enabled=False)},
        )
        self.assertFalse(is_event_enabled(config, "session-start"))


class TestGetEnabledPlatforms(unittest.TestCase):
    """Test enabled platforms resolution."""

    def test_no_platforms(self):
        config = FullNotificationConfig(enabled=True)
        self.assertEqual(get_enabled_platforms(config, "session-start"), [])

    def test_discord_enabled(self):
        config = FullNotificationConfig(
            enabled=True,
            discord=DiscordNotificationConfig(
                enabled=True, webhook_url="https://discord.com/test"
            ),
        )
        platforms = get_enabled_platforms(config, "session-start")
        self.assertIn("discord", platforms)

    def test_disabled_config_returns_empty(self):
        config = FullNotificationConfig(enabled=False)
        self.assertEqual(get_enabled_platforms(config, "session-start"), [])


class TestBuildConfigFromEnv(unittest.TestCase):
    """Test config building from environment variables."""

    @patch.dict(os.environ, {}, clear=True)
    def test_no_env_returns_none(self):
        self.assertIsNone(build_config_from_env())

    @patch.dict(
        os.environ,
        {
            "OMX_DISCORD_NOTIFIER_BOT_TOKEN": "test-token",
            "OMX_DISCORD_NOTIFIER_CHANNEL": "123456",
        },
        clear=True,
    )
    def test_discord_bot_from_env(self):
        config = build_config_from_env()
        self.assertIsNotNone(config)
        self.assertTrue(config.enabled)
        self.assertIsNotNone(config.discord_bot)
        self.assertEqual(config.discord_bot.bot_token, "test-token")

    @patch.dict(
        os.environ,
        {
            "OMX_TELEGRAM_BOT_TOKEN": "tok",
            "OMX_TELEGRAM_CHAT_ID": "123",
        },
        clear=True,
    )
    def test_telegram_from_env(self):
        config = build_config_from_env()
        self.assertIsNotNone(config)
        self.assertTrue(config.telegram.enabled)


class TestProfileResolution(unittest.TestCase):
    """Test profile resolution."""

    def test_no_profiles(self):
        block = NotificationsBlock(enabled=True)
        self.assertIsNone(resolve_profile_config(block))

    def test_resolve_named_profile(self):
        profile = FullNotificationConfig(
            enabled=True,
            discord=DiscordNotificationConfig(
                enabled=True, webhook_url="https://discord.com/test"
            ),
        )
        block = NotificationsBlock(
            enabled=True,
            profiles={"dev": profile},
        )
        result = resolve_profile_config(block, "dev")
        self.assertIsNotNone(result)
        self.assertTrue(result.discord.enabled)

    def test_missing_profile_returns_none(self):
        block = NotificationsBlock(
            enabled=True,
            profiles={"dev": FullNotificationConfig(enabled=True)},
        )
        with self.assertWarns(UserWarning):
            result = resolve_profile_config(block, "prod")
        self.assertIsNone(result)


class TestFormatter(unittest.TestCase):
    """Test notification message formatters."""

    def _make_payload(self, **kwargs) -> FullNotificationPayload:
        defaults = {
            "event": "session-start",
            "session_id": "test-session",
            "timestamp": "2024-01-15T10:30:00Z",
            "project_name": "my-project",
        }
        defaults.update(kwargs)
        return FullNotificationPayload(**defaults)

    def test_format_session_start(self):
        msg = format_session_start(self._make_payload())
        self.assertIn("Session Started", msg)
        self.assertIn("test-session", msg)
        self.assertIn("my-project", msg)

    def test_format_session_end(self):
        msg = format_session_end(
            self._make_payload(
                event="session-end",
                duration_ms=125000,
                reason="completed",
            )
        )
        self.assertIn("Session Ended", msg)
        self.assertIn("2m 5s", msg)
        self.assertIn("completed", msg)

    def test_format_session_idle(self):
        msg = format_session_idle(self._make_payload(event="session-idle"))
        self.assertIn("Session Idle", msg)
        self.assertIn("waiting for input", msg)

    def test_format_ask_user_question(self):
        msg = format_ask_user_question(
            self._make_payload(
                event="ask-user-question",
                question="What should I do?",
            )
        )
        self.assertIn("Input Needed", msg)
        self.assertIn("What should I do?", msg)

    def test_format_session_stop(self):
        msg = format_session_stop(
            self._make_payload(
                event="session-stop",
                active_mode="ralph",
                iteration=3,
                max_iterations=10,
            )
        )
        self.assertIn("Session Continuing", msg)
        self.assertIn("ralph", msg)
        self.assertIn("3/10", msg)

    def test_format_notification_dispatch(self):
        msg = format_notification(self._make_payload(event="session-start"))
        self.assertIn("Session Started", msg)

    def test_format_notification_unknown_event(self):
        p = self._make_payload(event="unknown", message="custom msg")
        msg = format_notification(p)
        self.assertEqual(msg, "custom msg")


class TestParseTmuxTail(unittest.TestCase):
    """Test tmux tail parsing."""

    def test_strips_ansi(self):
        raw = "\x1b[32mHello\x1b[0m world"
        self.assertEqual(parse_tmux_tail(raw), "Hello world")

    def test_removes_spinner_lines(self):
        raw = "\u25cf Loading...\nReal output here"
        self.assertNotIn("Loading", parse_tmux_tail(raw))
        self.assertIn("Real output", parse_tmux_tail(raw))

    def test_removes_bare_prompts(self):
        raw = "Real line\n>\n$\nAnother line"
        result = parse_tmux_tail(raw)
        self.assertNotIn("\n>", result)

    def test_empty_input(self):
        self.assertEqual(parse_tmux_tail(""), "")

    def test_groups_continuation_lines(self):
        raw = "Header\n  indented\n  also indented\nNext"
        result = parse_tmux_tail(raw)
        self.assertIn("Header", result)
        self.assertIn("indented", result)


class TestTemplateEngine(unittest.TestCase):
    """Test template interpolation engine."""

    def _make_payload(self, **kwargs) -> FullNotificationPayload:
        defaults = {
            "event": "session-start",
            "session_id": "test-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "project_name": "my-project",
        }
        defaults.update(kwargs)
        return FullNotificationPayload(**defaults)

    def test_simple_interpolation(self):
        result = interpolate_template("Session: {{sessionId}}", self._make_payload())
        self.assertEqual(result, "Session: test-123")

    def test_conditional_true(self):
        result = interpolate_template(
            "{{#if question}}Q: {{question}}{{/if}}",
            self._make_payload(question="How?"),
        )
        self.assertEqual(result, "Q: How?")

    def test_conditional_false(self):
        result = interpolate_template(
            "{{#if question}}Q: {{question}}{{/if}}",
            self._make_payload(),
        )
        self.assertEqual(result, "")

    def test_computed_duration(self):
        result = interpolate_template(
            "Duration: {{duration}}",
            self._make_payload(duration_ms=90000),
        )
        self.assertEqual(result, "Duration: 1m 30s")

    def test_validate_template_valid(self):
        valid, unknown = validate_template("{{sessionId}} {{duration}}")
        self.assertTrue(valid)
        self.assertEqual(unknown, [])

    def test_validate_template_unknown(self):
        valid, unknown = validate_template("{{fooBar}}")
        self.assertFalse(valid)
        self.assertIn("fooBar", unknown)

    def test_default_templates_exist(self):
        for event in NotificationEvent:
            template = get_default_template(event)
            self.assertTrue(len(template) > 0)

    def test_compute_variables(self):
        payload = self._make_payload(
            agents_spawned=5,
            agents_completed=3,
            modes_used=["ralph", "autopilot"],
        )
        v = compute_template_variables(payload)
        self.assertEqual(v["agentDisplay"], "3/5 completed")
        self.assertEqual(v["modesDisplay"], "ralph, autopilot")
        self.assertEqual(v["projectDisplay"], "my-project")


class TestHookConfig(unittest.TestCase):
    """Test hook notification config."""

    def setUp(self):
        reset_hook_config_cache()

    def test_resolve_event_template_none(self):
        self.assertIsNone(resolve_event_template(None, "session-start", "discord"))

    def test_resolve_event_template_event_level(self):
        config = HookNotificationConfig(
            enabled=True,
            events={
                "session-start": HookEventConfig(
                    enabled=True, template="Hello {{sessionId}}"
                )
            },
        )
        result = resolve_event_template(config, "session-start", "discord")
        self.assertEqual(result, "Hello {{sessionId}}")

    def test_resolve_event_template_platform_override(self):
        config = HookNotificationConfig(
            enabled=True,
            events={
                "session-start": HookEventConfig(
                    enabled=True,
                    template="default",
                    platforms={
                        "discord": PlatformTemplateOverride(template="discord specific")
                    },
                ),
            },
        )
        result = resolve_event_template(config, "session-start", "discord")
        self.assertEqual(result, "discord specific")

    def test_resolve_event_template_fallback(self):
        config = HookNotificationConfig(
            enabled=True,
            default_template="fallback",
        )
        result = resolve_event_template(config, "session-start", "discord")
        self.assertEqual(result, "fallback")

    def test_merge_hook_config(self):
        hook = HookNotificationConfig(
            enabled=True,
            events={"session-start": HookEventConfig(enabled=False)},
        )
        notif = FullNotificationConfig(
            enabled=True,
            events={"session-start": EventNotificationConfig(enabled=True)},
        )
        merged = merge_hook_config_into_notification_config(hook, notif)
        self.assertFalse(merged.events["session-start"].enabled)


class TestLifecycleDedupe(unittest.TestCase):
    """Test lifecycle event deduplication."""

    def test_should_dedupe(self):
        self.assertTrue(should_dedupe_lifecycle_notification("session-start"))
        self.assertTrue(should_dedupe_lifecycle_notification("session-end"))
        self.assertFalse(should_dedupe_lifecycle_notification("session-idle"))
        self.assertFalse(should_dedupe_lifecycle_notification("ask-user-question"))

    def test_fingerprint_stable(self):
        fp1 = create_lifecycle_broadcast_fingerprint({"a": 1, "b": 2})
        fp2 = create_lifecycle_broadcast_fingerprint({"b": 2, "a": 1})
        self.assertEqual(fp1, fp2)

    def test_should_send_first_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = FullNotificationPayload(
                event="session-start",
                session_id="test",
            )
            self.assertTrue(should_send_lifecycle_notification(tmpdir, payload))

    def test_dedupe_blocks_repeat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = FullNotificationPayload(
                event="session-start",
                session_id="test",
            )
            now = time.time() * 1000
            record_lifecycle_notification_sent(tmpdir, payload, now)
            self.assertFalse(
                should_send_lifecycle_notification(tmpdir, payload, now + 1000)
            )


class TestIdleCooldown(unittest.TestCase):
    """Test idle notification cooldown."""

    @patch.dict(os.environ, {"OMX_IDLE_COOLDOWN_SECONDS": "0"})
    def test_cooldown_zero_always_sends(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(should_send_idle_notification(tmpdir))

    def test_first_send_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(should_send_idle_notification(tmpdir, "session1"))

    def test_fingerprint_blocks_repeat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            record_idle_notification_sent(tmpdir, "s1", "fp1")
            self.assertFalse(should_send_idle_notification(tmpdir, "s1", "fp1"))
            self.assertTrue(should_send_idle_notification(tmpdir, "s1", "fp2"))


class TestDispatchCooldown(unittest.TestCase):
    """Test dispatch notification cooldown."""

    @patch.dict(os.environ, {"OMX_DISPATCH_COOLDOWN_SECONDS": "0"})
    def test_cooldown_zero_always_sends(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(should_send_dispatch_notification(tmpdir))

    def test_first_send_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(should_send_dispatch_notification(tmpdir, "session1"))


class TestSessionRegistry(unittest.TestCase):
    """Test session registry operations."""

    def test_register_and_lookup(self):
        import omx.notifications.session_registry as reg

        original_path = reg._REGISTRY_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                reg._REGISTRY_PATH = Path(tmpdir) / "test-registry.jsonl"
                mapping = SessionMapping(
                    platform="discord-bot",
                    message_id="msg123",
                    session_id="sess456",
                    tmux_pane_id="%0",
                    tmux_session_name="main",
                    event="session-start",
                    created_at="2024-01-15T10:30:00Z",
                )
                self.assertTrue(register_message(mapping))
                result = lookup_by_message_id("discord-bot", "msg123")
                self.assertIsNotNone(result)
                self.assertEqual(result.session_id, "sess456")

                result_none = lookup_by_message_id("discord-bot", "nonexistent")
                self.assertIsNone(result_none)
        finally:
            reg._REGISTRY_PATH = original_path

    def test_remove_session(self):
        import omx.notifications.session_registry as reg

        original_path = reg._REGISTRY_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                reg._REGISTRY_PATH = Path(tmpdir) / "test-registry.jsonl"
                register_message(
                    SessionMapping(
                        platform="telegram",
                        message_id="m1",
                        session_id="s1",
                        tmux_pane_id="%0",
                        tmux_session_name="main",
                        event="session-start",
                        created_at="2024-01-15T10:30:00Z",
                    )
                )
                remove_session("s1")
                self.assertIsNone(lookup_by_message_id("telegram", "m1"))
        finally:
            reg._REGISTRY_PATH = original_path


class TestSessionStatus(unittest.TestCase):
    """Test session status functions."""

    def test_is_status_command(self):
        self.assertTrue(is_discord_status_command("status"))
        self.assertTrue(is_discord_status_command("  STATUS  "))
        self.assertFalse(is_discord_status_command("hello"))
        self.assertFalse(is_discord_status_command(""))


class TestReplyListener(unittest.TestCase):
    """Test reply listener utilities."""

    def test_sanitize_reply_input(self):
        result = sanitize_reply_input("hello\nworld")
        self.assertEqual(result, "hello world")

    def test_sanitize_escapes_backticks(self):
        result = sanitize_reply_input("`test`")
        self.assertIn("\\`", result)

    def test_sanitize_escapes_dollar(self):
        result = sanitize_reply_input("$(cmd)")
        self.assertIn("\\$(", result)

    def test_sanitize_control_chars(self):
        result = sanitize_reply_input("hello\x00\x01world")
        self.assertEqual(result, "helloworld")

    def test_redact_sensitive_tokens(self):
        result = redact_sensitive_tokens("token sk-proj-abcdef123")
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("abcdef123", result)

    def test_redact_github_pat(self):
        result = redact_sensitive_tokens("token ghp_abc123xyz")
        self.assertIn("[REDACTED]", result)

    def test_format_acknowledgement_with_summary(self):
        result = format_reply_acknowledgement("Some output")
        self.assertIn("Injected into Codex CLI session", result)
        self.assertIn("Some output", result)

    def test_format_acknowledgement_no_summary(self):
        result = format_reply_acknowledgement(None)
        self.assertIn("unavailable", result)

    def test_rate_limiter(self):
        limiter = RateLimiter(2)
        self.assertTrue(limiter.can_proceed())
        self.assertTrue(limiter.can_proceed())
        self.assertFalse(limiter.can_proceed())
        limiter.reset()
        self.assertTrue(limiter.can_proceed())


class TestTempContract(unittest.TestCase):
    """Test temporary notification contract."""

    def test_parse_basic(self):
        result = parse_notify_temp_contract_from_args(
            ["--notify-temp", "--discord", "other-arg"],
            {},
        )
        self.assertTrue(result.contract.active)
        self.assertIn("discord", result.contract.canonical_selectors)
        self.assertEqual(result.passthrough_args, ["other-arg"])
        self.assertEqual(result.contract.source, NotifyTempSource.CLI)

    def test_parse_no_flags(self):
        result = parse_notify_temp_contract_from_args(["arg1", "arg2"], {})
        self.assertFalse(result.contract.active)

    def test_serialize_roundtrip(self):
        contract = NotifyTempContract(
            active=True,
            selectors=["discord"],
            canonical_selectors=["discord"],
            warnings=[],
            source=NotifyTempSource.CLI,
        )
        serialized = serialize_notify_temp_contract(contract)
        parsed = json.loads(serialized)
        self.assertTrue(parsed["active"])

    def test_is_notify_temp_env_active(self):
        self.assertTrue(is_notify_temp_env_active({"OMX_NOTIFY_TEMP": "1"}))
        self.assertFalse(is_notify_temp_env_active({"OMX_NOTIFY_TEMP": "0"}))
        self.assertFalse(is_notify_temp_env_active({}))

    def test_read_from_env(self):
        contract = NotifyTempContract(
            active=True,
            selectors=["discord"],
            canonical_selectors=["discord"],
            warnings=[],
            source=NotifyTempSource.CLI,
        )
        env = {"OMX_NOTIFY_TEMP_CONTRACT": serialize_notify_temp_contract(contract)}
        result = read_notify_temp_contract_from_env(env)
        self.assertIsNotNone(result)
        self.assertTrue(result.active)

    def test_get_builtin_selectors(self):
        contract = NotifyTempContract(
            active=True,
            selectors=["discord", "custom:foo"],
            canonical_selectors=["discord", "custom:foo"],
            warnings=[],
            source=NotifyTempSource.CLI,
        )
        builtins = get_temp_builtin_selectors(contract)
        self.assertEqual(builtins, {"discord"})

    def test_openclaw_selected(self):
        contract = NotifyTempContract(
            active=True,
            selectors=["openclaw:default"],
            canonical_selectors=["openclaw:default"],
            warnings=[],
            source=NotifyTempSource.CLI,
        )
        self.assertTrue(is_openclaw_selected_in_temp_contract(contract))

    def test_openclaw_not_selected(self):
        contract = NotifyTempContract(
            active=True,
            selectors=["discord"],
            canonical_selectors=["discord"],
            warnings=[],
            source=NotifyTempSource.CLI,
        )
        self.assertFalse(is_openclaw_selected_in_temp_contract(contract))


class TestTmuxDetector(unittest.TestCase):
    """Test tmux detector utilities."""

    def test_build_capture_pane_argv(self):
        argv = build_capture_pane_argv("%3", 15)
        self.assertIn("-t", argv)
        self.assertIn("%3", argv)
        self.assertIn("-S", argv)

    def test_analyze_pane_content_codex(self):
        result = analyze_pane_content("Running Codex CLI agent")
        self.assertTrue(result.has_codex)
        self.assertGreaterEqual(result.confidence, 0.5)

    def test_analyze_pane_content_empty(self):
        result = analyze_pane_content("")
        self.assertFalse(result.has_codex)
        self.assertEqual(result.confidence, 0.0)

    def test_analyze_pane_content_rate_limit(self):
        result = analyze_pane_content("Error: rate limit exceeded (429)")
        self.assertTrue(result.has_rate_limit_message)

    def test_build_send_pane_argvs(self):
        argvs = build_send_pane_argvs("%0", "hello", press_enter=True)
        self.assertEqual(len(argvs), 3)  # text + 2x C-m
        self.assertIn("-l", argvs[0])

    def test_build_send_pane_argvs_no_enter(self):
        argvs = build_send_pane_argvs("%0", "hello", press_enter=False)
        self.assertEqual(len(argvs), 1)

    def test_newlines_replaced_in_send(self):
        argvs = build_send_pane_argvs("%0", "line1\nline2", press_enter=False)
        self.assertNotIn("\n", argvs[0][-1])


class TestTmuxNotify(unittest.TestCase):
    """Test tmux notification utilities."""

    def test_sanitize_tmux_alert_text_none(self):
        self.assertIsNone(sanitize_tmux_alert_text(None))

    def test_sanitize_tmux_alert_text_empty(self):
        self.assertIsNone(sanitize_tmux_alert_text(""))

    def test_sanitize_tmux_alert_text_removes_metadata(self):
        raw = "[OMX#0.15.0] | turns:5\nReal error output"
        result = sanitize_tmux_alert_text(raw)
        self.assertIsNotNone(result)
        self.assertIn("Real error", result)


class TestNotifier(unittest.TestCase):
    """Test legacy notifier."""

    def test_build_desktop_args_linux(self):
        result = _build_desktop_args("Title", "Message", "linux")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "notify-send")

    def test_build_desktop_args_darwin(self):
        result = _build_desktop_args("Title", "Message", "darwin")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "osascript")

    def test_build_desktop_args_win32(self):
        result = _build_desktop_args("Title", "Message", "win32")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "powershell")

    def test_build_desktop_args_unknown(self):
        result = _build_desktop_args("Title", "Message", "freebsd")
        self.assertIsNone(result)


class TestDispatcherValidation(unittest.TestCase):
    """Test dispatcher validation without network calls."""

    def test_send_discord_not_configured(self):
        config = DiscordNotificationConfig(enabled=False)
        payload = FullNotificationPayload(event="session-start", session_id="t")
        result = send_discord(config, payload)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Not configured")

    def test_send_discord_invalid_url(self):
        config = DiscordNotificationConfig(
            enabled=True, webhook_url="http://evil.com/hook"
        )
        payload = FullNotificationPayload(event="session-start", session_id="t")
        result = send_discord(config, payload)
        self.assertFalse(result.success)
        self.assertIn("Invalid", result.error)

    def test_send_discord_bot_missing_token(self):
        config = DiscordBotNotificationConfig(enabled=True)
        payload = FullNotificationPayload(event="session-start", session_id="t")
        result = send_discord_bot(config, payload)
        self.assertFalse(result.success)

    def test_send_telegram_invalid_token(self):
        config = TelegramNotificationConfig(
            enabled=True, bot_token="bad", chat_id="123"
        )
        payload = FullNotificationPayload(event="session-start", session_id="t")
        result = send_telegram(config, payload)
        self.assertFalse(result.success)
        self.assertIn("Invalid", result.error)

    def test_send_slack_invalid_url(self):
        config = SlackNotificationConfig(enabled=True, webhook_url="http://evil.com")
        payload = FullNotificationPayload(event="session-start", session_id="t")
        result = send_slack(config, payload)
        self.assertFalse(result.success)

    def test_send_webhook_not_https(self):
        config = WebhookNotificationConfig(enabled=True, url="http://example.com")
        payload = FullNotificationPayload(event="session-start", session_id="t")
        result = send_webhook(config, payload)
        self.assertFalse(result.success)
        self.assertIn("HTTPS", result.error)


class TestFormatDuration(unittest.TestCase):
    """Test duration formatting."""

    def test_seconds(self):
        p = FullNotificationPayload(
            event="session-end", session_id="t", duration_ms=5000
        )
        msg = format_session_end(p)
        self.assertIn("5s", msg)

    def test_minutes(self):
        p = FullNotificationPayload(
            event="session-end", session_id="t", duration_ms=125000
        )
        msg = format_session_end(p)
        self.assertIn("2m 5s", msg)

    def test_hours(self):
        p = FullNotificationPayload(
            event="session-end", session_id="t", duration_ms=3725000
        )
        msg = format_session_end(p)
        self.assertIn("1h 2m 5s", msg)


if __name__ == "__main__":
    unittest.main()
