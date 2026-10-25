#!/usr/bin/env python3
import contextlib
import io
import json
import os
import sys
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

        current_state = {
            "system/send_security_reports": "0",
            "secure/send_action_app_error": "0",
            "global/activity_starts_logging_enabled": "0",
            "system/touch_prestart_opt_config": "{featureDisable:true}",
        }
        executed_cmds = []

        def sh_side_effect(serial, cmd, check=False):
            executed_cmds.append(cmd)
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
            if cmd.startswith("settings delete "):
                parts = cmd.split()
                key = f"{parts[2]}/{parts[3]}"
                current_state.pop(key, None)
                return ""
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

    @patch("tool.run.info", return_value={"model": "23049RAD8C", "brand": "Redmi"})
    @patch("tool.run.sh")
    def test_miui_opt_restore_verifies_readback_and_preserves_backup_on_failure(self, mock_sh, mock_info):
        bdir = Path(self.backups_dir, "23049RAD8C-20261025-1200")
        bdir.mkdir(parents=True)
        backup_file = bdir / "miui-opt-before.json"
        initial_data = {
            "serial": "test-serial",
            "saved_at": "2026-10-25T12:00:00",
            "settings": {
                "system/send_security_reports": "1",
                "secure/send_action_app_error": "0",
                "global/activity_starts_logging_enabled": "1",
                "system/touch_prestart_opt_config": "{featureDisable:false}",
            },
        }
        backup_file.write_text(json.dumps(initial_data), encoding="utf-8")

        # 模拟读回始终为 "0"，导致第一项 send_security_reports (期望 1) 校验失败
        def sh_side_effect(serial, cmd, check=False):
            if cmd.startswith("settings get "):
                return "0"
            return ""

        mock_sh.side_effect = sh_side_effect

        with self.assertRaisesRegex(RuntimeError, "还原校验失败"):
            run.miui_opt_apply("test-serial", turn_off=False)

        # 校验失败时必须保留备份文件
        self.assertTrue(backup_file.exists())

    def test_miui_opt_restore_no_backup_raises(self):
        with self.assertRaisesRegex(RuntimeError, "未找到本设备的 miui-opt 原值备份"):
            run.miui_opt_restore("non-existent-device")


class MiuiOptCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.backups_dir = self.tmp_dir.name
        self.patcher = patch.object(run, "BACKUPS", self.backups_dir)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    @patch("tool.run.info", return_value={"brand": "OPPO", "model": "PLP120"})
    def test_miui_opt_cli_non_xiaomi_rejected(self, mock_info):
        with patch.object(sys, "argv", ["run.py", "-s", "s1", "miui-opt", "check"]):
            with self.assertRaises(SystemExit) as cm:
                run.main()
        self.assertIn("仅适用于小米", str(cm.exception))

    @patch("tool.run.info", return_value={"brand": "Xiaomi", "model": "23049RAD8C"})
    @patch("tool.run.miui_opt_status")
    def test_miui_opt_cli_check_displays_status(self, mock_status, mock_info):
        mock_status.return_value = {
            "settings": {
                "send_security_reports": {"closed": True, "value": "0", "target": "0", "desc": "安全中心安全报告上传"},
                "send_action_app_error": {"closed": False, "value": "1", "target": "0", "desc": "崩溃与错误报告上传"},
                "activity_starts_logging_enabled": {"closed": True, "value": "0", "target": "0", "desc": "Activity 启动打点日志"},
                "touch_prestart_opt_config": {"closed": False, "value": "{featureDisable:false}", "target": "{featureDisable:true}", "desc": "触控预加载优化"},
            },
            "packages": {
                "com.miui.analytics": {"installed": True, "suspended": False, "status": "未挂起", "desc": "分析与统计服务"},
                "com.miui.daemon": {"installed": True, "suspended": True, "status": "已挂起", "desc": "性能监控守护进程"},
            }
        }
        buf = io.StringIO()
        with patch.object(sys, "argv", ["run.py", "-s", "s1", "miui-opt", "check"]), \
             contextlib.redirect_stdout(buf):
            run.main()
        out = buf.getvalue()
        self.assertIn("小米专属优化项与监控组件状态：", out)
        self.assertIn("安全中心安全报告上传 (send_security_reports): 已关闭", out)
        self.assertIn("崩溃与错误报告上传 (send_action_app_error): 未关闭", out)
        self.assertIn("分析与统计服务 (com.miui.analytics): 未挂起", out)
        self.assertIn("性能监控守护进程 (com.miui.daemon): 已挂起", out)
        self.assertIn("一键优化：python3 tool/run.py -s s1 miui-opt off", out)

        mock_status.return_value["settings"]["send_action_app_error"]["closed"] = True
        mock_status.return_value["settings"]["touch_prestart_opt_config"]["closed"] = True
        buf2 = io.StringIO()
        with patch.object(sys, "argv", ["run.py", "-s", "s1", "miui-opt", "check"]), \
             contextlib.redirect_stdout(buf2):
            run.main()
        out2 = buf2.getvalue()
        self.assertIn("已优化完成。恢复：python3 tool/run.py -s s1 miui-opt on", out2)

    @patch("tool.run.info", return_value={"brand": "Redmi", "model": "23049RAD8C"})
    @patch("tool.run.miui_opt_apply")
    @patch("tool.run.miui_opt_status")
    def test_miui_opt_cli_off_and_on(self, mock_status, mock_apply, mock_info):
        mock_apply.return_value = {"ok": True, "action": "off", "backup": os.path.join(self.backups_dir, "miui-opt-before.json")}
        mock_status.return_value = {
            "settings": {},
            "packages": {
                "com.miui.analytics": {"installed": True, "suspended": False, "status": "未挂起", "desc": "分析与统计服务"},
                "com.miui.daemon": {"installed": False, "suspended": False, "status": "未安装", "desc": "性能监控守护进程"},
            }
        }
        buf = io.StringIO()
        with patch.object(sys, "argv", ["run.py", "-s", "s1", "miui-opt", "off"]), \
             contextlib.redirect_stdout(buf):
            run.main()
        mock_apply.assert_called_with("s1", turn_off=True)
        out = buf.getvalue()
        self.assertIn("小米专属优化已生效", out)
        self.assertIn("原值已备份至", out)
        self.assertIn("suspend com.miui.analytics", out)
        self.assertIn("miui-opt on", out)

        mock_apply.reset_mock()
        mock_apply.return_value = {"ok": True, "action": "on"}
        buf_on = io.StringIO()
        with patch.object(sys, "argv", ["run.py", "-s", "s1", "miui-opt", "on"]), \
             contextlib.redirect_stdout(buf_on):
            run.main()
        mock_apply.assert_called_with("s1", turn_off=False)
        self.assertIn("已恢复原值", buf_on.getvalue())

        with patch.object(sys, "argv", ["run.py", "-s", "s1", "miui-opt", "unknown"]):
            with self.assertRaises(SystemExit) as cm:
                run.main()
        self.assertIn("check / off / on", str(cm.exception))

    @patch("tool.run.locked", return_value=False)
    @patch("tool.run.sh", return_value="")
    @patch("tool.run.quickapps", return_value=[])
    @patch("tool.run.recipe_for", return_value=(None, False))
    @patch("tool.run.info")
    @patch("tool.run.miui_opt_status")
    def test_check_command_shows_miui_section_only_for_xiaomi(self, mock_status, mock_info, *mocks):
        mock_status.return_value = {
            "settings": {
                "send_security_reports": {"closed": True, "value": "0", "desc": "安全中心安全报告上传"},
                "send_action_app_error": {"closed": False, "value": "1", "desc": "崩溃与错误报告上传"},
                "activity_starts_logging_enabled": {"closed": True, "value": "0", "desc": "Activity 启动打点日志"},
                "touch_prestart_opt_config": {"closed": False, "value": "{featureDisable:false}", "desc": "触控预加载优化"},
            },
            "packages": {
                "com.miui.analytics": {"installed": True, "suspended": True, "status": "已挂起", "desc": "分析与统计服务"},
                "com.miui.daemon": {"installed": True, "suspended": False, "status": "未挂起", "desc": "性能监控守护进程"},
            }
        }
        mock_info.return_value = {"brand": "Xiaomi", "model": "23049RAD8C", "name": "Xiaomi 13", "build": "OS1.0", "android": "14"}
        buf = io.StringIO()
        with patch.object(sys, "argv", ["run.py", "-s", "s1", "check"]), \
             contextlib.redirect_stdout(buf):
            run.main()
        out = buf.getvalue()
        self.assertIn("小米专属优化（遥测与触控省电）：", out)
        self.assertIn("安全中心安全报告上传 (send_security_reports)：已关闭 (0)", out)
        self.assertIn("崩溃与错误报告上传 (send_action_app_error)：未关闭 (1)", out)
        self.assertIn("分析与统计服务 (com.miui.analytics)：已挂起", out)
        self.assertIn("性能监控守护进程 (com.miui.daemon)：未挂起", out)
        self.assertIn("一键优化遥测与触控：python3 tool/run.py -s s1 miui-opt off", out)

        mock_info.return_value = {"brand": "OPPO", "model": "PJG110", "name": "Find N3", "build": "ColorOS", "android": "14"}
        buf2 = io.StringIO()
        with patch.object(sys, "argv", ["run.py", "-s", "s1", "check"]), \
             contextlib.redirect_stdout(buf2):
            run.main()
        out2 = buf2.getvalue()
        self.assertNotIn("小米专属优化", out2)

    @patch("tool.run.locked", return_value=False)
    @patch("tool.run.sh", return_value="")
    @patch("tool.run.quickapps", return_value=[])
    @patch("tool.run.recipe_for", return_value=(None, False))
    @patch("tool.run.info")
    @patch("tool.run.miui_opt_status")
    def test_status_json_includes_miui_opt_for_xiaomi(self, mock_status, mock_info, *mocks):
        mock_status.return_value = {
            "settings": {"send_security_reports": {"closed": True}},
            "packages": {"com.miui.analytics": {"status": "已挂起"}}
        }
        mock_info.return_value = {"brand": "POCO", "model": "F5", "name": "POCO F5", "build": "OS1.0", "android": "14"}
        buf = io.StringIO()
        with patch.object(sys, "argv", ["run.py", "-s", "s1", "status-json"]), \
             contextlib.redirect_stdout(buf):
            run.main()
        data = json.loads(buf.getvalue().strip())
        self.assertIn("miui_opt", data)
        self.assertIsNotNone(data["miui_opt"])
        self.assertEqual(data["miui_opt"]["settings"]["send_security_reports"]["closed"], True)

        mock_info.return_value = {"brand": "vivo", "model": "V2300", "name": "X100", "build": "OriginOS", "android": "14"}
        buf2 = io.StringIO()
        with patch.object(sys, "argv", ["run.py", "-s", "s1", "status-json"]), \
             contextlib.redirect_stdout(buf2):
            run.main()
        data2 = json.loads(buf2.getvalue().strip())
        self.assertIn("miui_opt", data2)
        self.assertIsNone(data2["miui_opt"])


if __name__ == "__main__":
    unittest.main()
