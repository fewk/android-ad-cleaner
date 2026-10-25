#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import tool.run as run


class MiuiOptCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.backups_dir = self.tmp_dir.name
        self.patcher = patch.object(run, "BACKUPS", self.backups_dir)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_is_xiaomi_family(self):
        self.assertTrue(run.is_xiaomi_family("Xiaomi"))
        self.assertTrue(run.is_xiaomi_family("Redmi"))
        self.assertTrue(run.is_xiaomi_family("POCO"))
        self.assertTrue(run.is_xiaomi_family("poco"))
        self.assertFalse(run.is_xiaomi_family("OPPO"))
        self.assertFalse(run.is_xiaomi_family("vivo"))
        self.assertFalse(run.is_xiaomi_family("HONOR"))
        self.assertFalse(run.is_xiaomi_family(""))
        self.assertFalse(run.is_xiaomi_family(None))

    @patch("tool.run.sh")
    @patch("tool.run.suspended")
    @patch("tool.run.installed")
    def test_miui_opt_status(self, mock_installed, mock_suspended, mock_sh):
        def sh_side_effect(serial, cmd, check=False):
            if "send_security_reports" in cmd:
                return "0"
            if "send_action_app_error" in cmd:
                return "1"
            if "activity_starts_logging_enabled" in cmd:
                return "0"
            if "touch_prestart_opt_config" in cmd:
                return "{featureDisable:true}"
            return ""

        mock_sh.side_effect = sh_side_effect
        mock_installed.side_effect = lambda s, pkg: pkg == "com.miui.analytics"
        mock_suspended.side_effect = lambda s, pkg: pkg == "com.miui.analytics"

        status = run.miui_opt_status("test-serial")
        settings = status["settings"]
        self.assertTrue(settings["send_security_reports"]["closed"])
        self.assertEqual(settings["send_security_reports"]["value"], "0")
        self.assertEqual(settings["send_security_reports"]["target"], "0")
        self.assertEqual(settings["send_security_reports"]["ns"], "system")
        self.assertIn("安全报告", settings["send_security_reports"]["desc"])

        self.assertFalse(settings["send_action_app_error"]["closed"])
        self.assertEqual(settings["send_action_app_error"]["value"], "1")
        self.assertEqual(settings["send_action_app_error"]["ns"], "secure")

        self.assertTrue(settings["activity_starts_logging_enabled"]["closed"])
        self.assertEqual(settings["activity_starts_logging_enabled"]["ns"], "global")

        self.assertTrue(settings["touch_prestart_opt_config"]["closed"])
        self.assertEqual(settings["touch_prestart_opt_config"]["ns"], "system")

        packages = status["packages"]
        self.assertIn("com.miui.analytics", packages)
        self.assertTrue(packages["com.miui.analytics"]["installed"])
        self.assertTrue(packages["com.miui.analytics"]["suspended"])
        self.assertEqual(packages["com.miui.analytics"]["status"], "已挂起")

        self.assertIn("com.miui.daemon", packages)
        self.assertFalse(packages["com.miui.daemon"]["installed"])
        self.assertEqual(packages["com.miui.daemon"]["status"], "未安装")

    @patch("tool.run.info", return_value={"model": "23049RAD8C", "brand": "Redmi"})
    @patch("tool.run.sh")
    def test_miui_opt_apply_off_creates_backup_and_verifies(self, mock_sh, mock_info):
        current_state = {
            "system/send_security_reports": "1",
            "secure/send_action_app_error": "1",
            "global/activity_starts_logging_enabled": "1",
            "system/touch_prestart_opt_config": "{featureDisable:false}",
        }

        def sh_side_effect(serial, cmd, check=False):
            if cmd.startswith("settings get "):
                parts = cmd.split()
                key = f"{parts[2]}/{parts[3]}"
                return current_state.get(key, "")
            if cmd.startswith("settings put "):
                parts = cmd.split(maxsplit=4)
                ns, k = parts[2], parts[3]
                val = parts[4] if len(parts) > 4 else ""
                current_state[f"{ns}/{k}"] = val.strip('"\'')
                return ""
            return ""

        mock_sh.side_effect = sh_side_effect

        res = run.miui_opt_apply("test-serial", turn_off=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["action"], "off")
        bpath = res["backup"]
        self.assertTrue(os.path.exists(bpath))

        with open(bpath, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["serial"], "test-serial")
        self.assertEqual(data["settings"]["system/send_security_reports"], "1")
        self.assertEqual(data["settings"]["secure/send_action_app_error"], "1")
        self.assertEqual(data["settings"]["global/activity_starts_logging_enabled"], "1")
        self.assertEqual(data["settings"]["system/touch_prestart_opt_config"], "{featureDisable:false}")

        # 验证写入的目标值
        self.assertEqual(current_state["system/send_security_reports"], "0")
        self.assertEqual(current_state["secure/send_action_app_error"], "0")
        self.assertEqual(current_state["global/activity_starts_logging_enabled"], "0")
        self.assertIn("featureDisable:true", current_state["system/touch_prestart_opt_config"])

    @patch("tool.run.info", return_value={"model": "23049RAD8C", "brand": "Redmi"})
    @patch("tool.run.sh")
    def test_miui_opt_apply_repeat_off_does_not_overwrite_backup(self, mock_sh, mock_info):
        # 先准备一个已存在的备份目录与文件
        bdir = Path(self.backups_dir, "23049RAD8C-20261025-1200")
        bdir.mkdir(parents=True)
        orig_backup = bdir / "miui-opt-before.json"
        initial_data = {
            "serial": "test-serial",
            "saved_at": "2026-10-25T12:00:00",
            "settings": {
                "system/send_security_reports": "1",
                "secure/send_action_app_error": "1",
                "global/activity_starts_logging_enabled": "1",
                "system/touch_prestart_opt_config": "{featureDisable:false}",
            },
        }
        orig_backup.write_text(json.dumps(initial_data), encoding="utf-8")

        # 当前设备上其实都已经是优化后的 0 了
        current_state = {
            "system/send_security_reports": "0",
            "secure/send_action_app_error": "0",
            "global/activity_starts_logging_enabled": "0",
            "system/touch_prestart_opt_config": "{featureDisable:true}",
        }

        def sh_side_effect(serial, cmd, check=False):
            if cmd.startswith("settings get "):
                parts = cmd.split()
                key = f"{parts[2]}/{parts[3]}"
                return current_state.get(key, "")
            return ""

        mock_sh.side_effect = sh_side_effect

        res = run.miui_opt_apply("test-serial", turn_off=True)
        self.assertTrue(res["ok"])

        # 再次读取备份文件，确认原值依然是 "1"，没有被 "0" 覆盖
        with open(orig_backup, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["settings"]["system/send_security_reports"], "1")
        self.assertEqual(data["saved_at"], "2026-10-25T12:00:00")

    @patch("tool.run.info", return_value={"model": "23049RAD8C", "brand": "Redmi"})
    @patch("tool.run.sh")
    def test_miui_opt_apply_verify_failure_raises(self, mock_sh, mock_info):
        # 模拟系统拒绝更改，settings get 回读依然是 1
        def sh_side_effect(serial, cmd, check=False):
            if cmd.startswith("settings get "):
                return "1"
            return ""

        mock_sh.side_effect = sh_side_effect

        with self.assertRaisesRegex(RuntimeError, "写入校验失败"):
            run.miui_opt_apply("test-serial", turn_off=True)

    @patch("tool.run.info", return_value={"model": "23049RAD8C", "brand": "Redmi"})
    @patch("tool.run.sh")
    def test_miui_opt_apply_on_restores_from_backup(self, mock_sh, mock_info):
        bdir = Path(self.backups_dir, "23049RAD8C-20261025-1200")
        bdir.mkdir(parents=True)
        backup_file = bdir / "miui-opt-before.json"
        initial_data = {
            "serial": "test-serial",
            "saved_at": "2026-10-25T12:00:00",
            "settings": {
                "system/send_security_reports": "1",
                "secure/send_action_app_error": "null",  # 原值是 null，还原应调用 settings delete
                "global/activity_starts_logging_enabled": "1",
                "system/touch_prestart_opt_config": "{featureDisable:false}",
            },
        }
        backup_file.write_text(json.dumps(initial_data), encoding="utf-8")

        executed_cmds = []

        def sh_side_effect(serial, cmd, check=False):
            executed_cmds.append(cmd)
            return ""

        mock_sh.side_effect = sh_side_effect

        res = run.miui_opt_apply("test-serial", turn_off=False)
        self.assertTrue(res["ok"])
        self.assertEqual(res["action"], "on")

        # 检查是否执行了 put 与 delete
        self.assertIn("settings put system send_security_reports 1", executed_cmds)
        self.assertIn("settings delete secure send_action_app_error", executed_cmds)
        self.assertIn("settings put global activity_starts_logging_enabled 1", executed_cmds)
        self.assertIn("settings put system touch_prestart_opt_config {featureDisable:false}", executed_cmds)

        # 还原后备份文件应被清理
        self.assertFalse(backup_file.exists())

    def test_miui_opt_restore_no_backup_raises(self):
        with self.assertRaisesRegex(RuntimeError, "未找到本设备的 miui-opt 原值备份"):
            run.miui_opt_restore("non-existent-device")


if __name__ == "__main__":
    unittest.main()
