"""Operate Samsung Calculator's visible keypad for one requested arithmetic operation."""
import operator
import re
import threading
import time
from decimal import Decimal

from . import phone, phone_fast, tools

PACKAGE = "com.sec.android.app.popupcalculator"
_LOCK = threading.Lock()
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_SYMBOLS = str.maketrans({"×": "*", "x": "*", "÷": "/", "−": "-", "٫": "."})
_OPERATIONS = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}
_KEYS = {
    "C": ("c", "ac", "clear", "clear all", "مسح", "مسح الكل"),
    "+": ("+", "plus", "جمع", "زائد"), "-": ("-", "minus", "طرح", "ناقص"),
    "*": ("*", "multiply", "times", "ضرب"), "/": ("/", "divide", "قسمة"),
    "=": ("=", "equals", "يساوي"), ".": (".", "decimal point", "فاصلة عشرية"),
}
_DISPLAY = re.compile(r"(?:حقل إدخال الحاسبة|calculator input(?: field)?|نتيجة|result)\s*[:：]?\s*", re.I)


def _normalize(text):
    return str(text).translate(_DIGITS).translate(_SYMBOLS).strip().lower()


def _expression(text):
    number = r"([0-9]{1,7}(?:\.[0-9]{1,6})?)"
    match = re.fullmatch(number + r"\s*([+*/-])\s*" + number, _normalize(text))
    if not match:
        raise tools.ToolError("Use two unsigned numbers and one of +, -, *, /, for example 37*19.")
    left, op, right = match.groups()
    if op == "/" and Decimal(right) == 0:
        raise tools.ToolError("Division by zero is undefined")
    return left + op + right, _OPERATIONS[op](Decimal(left), Decimal(right))


def _displayed(screen, expected):
    for line in screen.splitlines():
        entry = phone_fast.ITEM.match(line.strip())
        if not entry:
            continue
        label = _normalize(entry.group(2))
        field = _DISPLAY.match(label)
        if field:
            label = label[field.end():]
        elif label.endswith((" نتيجة الحساب", " calculation result")):
            label = label.rsplit(" ", 2)[0]
        elif "field" not in line.rpartition(" · ")[2].split(", "):
            continue
        label = label.replace("٬", ",").strip()
        if not re.fullmatch(r"-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", label):
            continue
        value = Decimal(label.replace(",", ""))
        if value == expected:
            return label
        if value and len(value.as_tuple().digits) >= 9 and abs(value - expected) <= abs(expected) * Decimal("1e-9"):
            return label
    return None


def _screen(response):
    screen = response.get("screen") or phone.call("screen").get("text", "")
    if phone_fast.foreground(screen) != PACKAGE:
        raise tools.ToolError("Samsung Calculator is not the foreground app")
    return screen


@tools.tool("calculator_app", "Use Samsung Calculator's actual keys and verify the displayed number. "
            "Use when the user requests an operation in their calculator app.",
            {"expression": tools._s("Two unsigned numbers with +, -, *, or /, e.g. 37*19")}, ["expression"], "phone")
def calculate(ctx, expression):
    expression, expected = _expression(expression)
    if not _LOCK.acquire(blocking=False):
        raise tools.ToolError("Calculator is already in use")
    try:
        screen = _screen(phone.call("open_app", name=PACKAGE))
        for key in "C" + expression + "=":
            if getattr(ctx, "cancel", None) is not None and ctx.cancel.is_set():
                raise tools.ToolError("cancelled")
            candidates = _KEYS.get(key, (key,))
            label = next((s for s in phone_fast.items(screen).values() if _normalize(s) in candidates), None)
            if label is None:
                raise tools.ToolError("Calculator key %r was not visible" % key)
            screen = _screen(phone.call("tap", text=label, exact_package=PACKAGE))
        shown = _displayed(screen, expected)
        for attempt in range(4):
            if shown is not None:
                break
            if attempt:
                time.sleep(0.3)
            observed = phone.call("screen")
            fresh = observed.get("text", "")
            if phone_fast.foreground(fresh) != PACKAGE:
                break
            # Native IDs restrict these values to the formula and result displays, excluding keypad digits.
            fields = observed.get("calculator_display") or []
            lines = ["[1] " + value + " · field" for value in fields if isinstance(value, str)]
            shown = _displayed("\n".join([fresh] + lines), expected)
        if shown is None:
            raise tools.ToolError("The result could not be verified on Samsung Calculator's display.")
        return "Samsung Calculator shows " + shown, {"displayed": shown, "expression": expression}
    except phone.PhoneError as exc:
        raise tools.ToolError(str(exc)) from exc
    finally:
        _LOCK.release()
