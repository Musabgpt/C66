"""The user's local date and time, sent with each request.

Python on Android has no time zone of its own and counts in UTC; the app passes the phone's zone in TZ (Setup.java),
and bionic reads it from Android's own tzdata. A model that knows the local time and UTC offset can answer "what day
is it", turn "at 9 tomorrow" into a correct scheduled time and see how long ago an earlier message was sent."""
import os
import time


def offset(t):
    minutes = int(t.tm_gmtoff or 0) // 60
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return "%s%02d:%02d" % (sign, minutes // 60, minutes % 60)


def note(now=None):
    t = time.localtime(now)
    zone = os.environ.get("TZ") or time.strftime("%Z", t) or "local"
    return "Local time: %s (UTC%s, %s)." % (time.strftime("%A %Y-%m-%d %H:%M", t), offset(t), zone)
