import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from . import tools
from . import calculator_app as calc


class CalculatorAppTests(unittest.TestCase):
    def screen(self, display="", package=calc.PACKAGE):
        keys = "C0123456789+−×÷=."
        lines = ["App: Calculator (%s)" % package, "[1] حقل إدخال الحاسبة %s · field" % display]
        return "\n".join(lines + ["[%s] %s · button, tap" % (i + 2, key) for i, key in enumerate(keys)])

    def test_one_tool_call_uses_named_native_buttons_and_reads_the_result(self):
        calls = []

        def call(action, **args):
            calls.append((action, args))
            return {"screen": self.screen("٧٠٣" if args.get("text") == "=" else "")}

        with patch.object(calc.phone, "call", side_effect=call):
            reply, meta = calc.calculate(SimpleNamespace(), "٣٧ × ١٩")
        self.assertIn("703", reply)
        self.assertEqual(meta["displayed"], "703")
        self.assertEqual(calls[0], ("open_app", {"name": calc.PACKAGE}))
        self.assertEqual([c[1] for c in calls[1:]], [
            {"text": key, "exact_package": calc.PACKAGE} for key in "C37×19="])

    def test_samsung_arabic_result_suffix(self):
        self.assertEqual(calc._displayed("[4] 703 نتيجة الحساب · text", 703), "703")

    def test_fresh_display_by_native_id_after_equals(self):
        def call(action, **args):
            if action == "screen":
                return {"text": self.screen(), "calculator_display": ["18"]}
            return {"screen": self.screen()}
        with patch.object(calc.phone, "call", side_effect=call):
            self.assertEqual(calc.calculate(None, "12+6")[1]["displayed"], "18")

    def test_keypad_number_is_not_the_result(self):
        with patch.object(calc.phone, "call", return_value={"screen": self.screen("42")}):
            with self.assertRaisesRegex(tools.ToolError, "verified"):
                calc.calculate(None, "3+4")

    def test_other_foreground_is_not_tapped(self):
        with patch.object(calc.phone, "call", return_value={"screen": self.screen(package="other.app")}) as call:
            with self.assertRaisesRegex(tools.ToolError, "foreground"):
                calc.calculate(None, "3+4")
            self.assertEqual(call.call_count, 1)

    def test_bad_input_never_opens_the_app(self):
        with patch.object(calc.phone, "call") as call:
            for expr in ("0/0", "1+2+3", "not a number"):
                with self.assertRaises(tools.ToolError):
                    calc.calculate(None, expr)
            call.assert_not_called()

    def test_cancel_stops_before_the_next_key(self):
        stop = threading.Event()
        def call(action, **args):
            if args.get("text") == "C":
                stop.set()
            return {"screen": self.screen()}
        with patch.object(calc.phone, "call", side_effect=call) as api:
            with self.assertRaisesRegex(tools.ToolError, "cancelled"):
                calc.calculate(SimpleNamespace(cancel=stop), "3+4")
            self.assertEqual(api.call_count, 2)
