from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


# 本地离线测试环境不安装完整后端依赖，只为被测通知模块提供导入桩。
sys.modules.setdefault("loguru", types.SimpleNamespace(logger=_Logger()))
services_package = types.ModuleType("common.services")
services_package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "common/services")
]
sys.modules.setdefault("common.services", services_package)
sys.modules.setdefault(
    "common.services.email_log_service",
    types.SimpleNamespace(record_email_log=lambda *args, **kwargs: None),
)

from common.utils.bark_notification import (  # noqa: E402
    DEFAULT_BARK_ICON_URL,
    prepare_bark_chat_notification,
)
from common.utils.notification_template import (  # noqa: E402
    validate_notification_templates,
)
from common.utils.notification_utils import send_bark_notification  # noqa: E402


def chat_context(*, account_id="account-1", remark="主账号", buyer="买家A"):
    return {
        "account_id": account_id,
        "account_remark": remark or "未知",
        "_account_remark_raw": remark,
        "buyer_nick": buyer,
        "message": "你好",
        "time": "2026-11-18 12:00:00",
    }


class BarkChatFormattingTests(unittest.IsolatedAsyncioTestCase):
    def test_default_title_falls_back_to_account_id_and_body_is_compact(self):
        config, body = prepare_bark_chat_notification(
            {"bark_chat_title_template": ""},
            "旧默认正文",
            chat_context(remark=""),
        )

        self.assertEqual(config["title"], "account-1-买家A")
        self.assertEqual(body, "消息: 你好\n时间: 2026-11-18 12:00:00")
        self.assertEqual(config["icon"], DEFAULT_BARK_ICON_URL)

    def test_same_remark_groups_do_not_collide_between_accounts(self):
        first, _ = prepare_bark_chat_notification(
            {}, "", chat_context(account_id="1001")
        )
        second, _ = prepare_bark_chat_notification(
            {}, "", chat_context(account_id="1002")
        )

        self.assertNotEqual(first["group"], second["group"])
        self.assertEqual(first["group"], "主账号 · 1001")
        self.assertEqual(second["group"], "主账号 · 1002")

    def test_custom_title_body_icon_and_group_are_preserved(self):
        stored_config = {
            "bark_chat_title_template": "{{buyer_nick}} → {{account_remark}}",
            "chat_template": "自定义 {{message}}",
            "icon": "https://example.test/icon.png",
            "group": "我的分组",
        }

        config, body = prepare_bark_chat_notification(
            stored_config,
            "自定义 你好",
            chat_context(),
        )

        self.assertEqual(config["title"], "买家A → 主账号")
        self.assertEqual(body, "自定义 你好")
        self.assertEqual(config["icon"], "https://example.test/icon.png")
        self.assertEqual(config["group"], "我的分组 · account-1")
        self.assertNotIn("title", stored_config)
        self.assertEqual(stored_config["group"], "我的分组")

    def test_legacy_generic_title_does_not_override_chat_title(self):
        config, _ = prepare_bark_chat_notification(
            {"title": "历史通用标题"}, "", chat_context()
        )

        self.assertEqual(config["title"], "主账号-买家A")

    def test_server_rejects_unknown_and_malformed_title_variables(self):
        unknown = validate_notification_templates(
            {"bark_chat_title_template": "{{account_id}}-{{buyer_nick}}"}
        )
        malformed = validate_notification_templates(
            {"bark_chat_title_template": "{{buyer_nick}"}
        )

        self.assertIn("account_id", unknown or "")
        self.assertIn("占位符必须使用", malformed or "")

    async def test_mocked_bark_request_payload(self):
        captured = {}

        class FakeResponse:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def text(self):
                return json.dumps({"code": 200})

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def post(self, url, *, json, timeout):
                captured.update(url=url, payload=json, timeout=timeout)
                return FakeResponse()

        config, body = prepare_bark_chat_notification(
            {
                "device_key": "test-key",
                "bark_chat_title_template": "",
                "sound": "minuet",
                "url": "https://example.test/chat",
            },
            "通用正文",
            chat_context(),
        )
        with patch(
            "common.utils.notification_utils.aiohttp.ClientSession",
            return_value=FakeSession(),
        ):
            success = await send_bark_notification(config, body)

        self.assertTrue(success)
        self.assertEqual(captured["url"], "https://api.day.app/push")
        self.assertEqual(
            captured["payload"],
            {
                "device_key": "test-key",
                "title": "主账号-买家A",
                "body": "消息: 你好\n时间: 2026-11-18 12:00:00",
                "sound": "minuet",
                "group": "主账号 · account-1",
                "icon": DEFAULT_BARK_ICON_URL,
                "url": "https://example.test/chat",
            },
        )


if __name__ == "__main__":
    unittest.main()
