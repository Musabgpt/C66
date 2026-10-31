"""The floating assistant follows a turn through a waiting events endpoint, and every request carries the local time.

- session.py counts every event a thread has had, so a cursor stays right after old events are dropped.
- GET /api/sessions/<id>/events?after=N&wait=S answers with only the events after the first N, as soon as one
  arrives or the turn ends (Service.wait_events, connectors/phone_live/service.py).
- The user's message carries "Local time: ..." (clock.py) when the app passed the phone's time zone (TZ)."""
import shutil


def apply(src, package, replace):
    for name in ("clock.py", "assistant_events_tests.py"):
        shutil.copyfile(src / name, package / name)

    # agent_prompt_lean.md now speaks of the local time and the alarm: threads pick up the new text
    replace(package / "agent_policy.py", "MARKER = 'MusabAI behavior profile v11'",
            "MARKER = 'MusabAI behavior profile v12'")

    replace(package / "session.py",
            "    def add_event(self, event):\n"
            "        self.events.append(event)\n",
            "    def add_event(self, event):\n"
            "        self.events.append(event)\n"
            "        self.event_total = getattr(self, \"event_total\", len(self.events) - 1) + 1\n")

    replace(package / "server.py",
            r'''            m = re.match(r"^/api/sessions/([\w\-]+)/changes$", path)
''',
            r'''            m = re.match(r"^/api/sessions/([\w\-]+)/events$", path)
            if m:
                return self._json(svc.wait_events(m.group(1), int(q.get("after") or 0), float(q.get("wait") or 0)))
            m = re.match(r"^/api/sessions/([\w\-]+)/changes$", path)
''')

    # A reply that says it did what no tool did is sent back once (agent.claims). Live on the phone, the free model
    # answered "ذكرني بعد ساعتين..." with "تم. سأذكرك..." and "**تم الحفظ**" and saved nothing: "تم" next to a full
    # stop or an asterisk went unnoticed, and the correction named no tool that saves a reminder.
    replace(package / "agent.py",
            '    r"(?:^|\\s)(?:تم|لقد|قمت|أنشأت|انشأت|كتبت|فتحت|أضفت|اضفت|عدلت|عدّلت|صلحت|أصلحت|حفظت|شغلت|شغّلت|حذفت)(?:\\s|$)",\n',
            '    r"(?<!\\w)(?:تم|تمت|لقد|قمت|أنشأت|انشأت|كتبت|فتحت|أضفت|اضفت|عدلت|عدّلت|صلحت|أصلحت|حفظت|شغلت|شغّلت|حذفت|"\n'
            '    r"ضبطت|سجلت|سجّلت|جدولت|أرسلت|ارسلت|بعثت)(?!\\w)",\n')
    replace(package / "agent.py",
            '                                              ("phone", "phone acts on the phone (action=open_app...)"))\n',
            '                                              ("scheduled_task_create", "scheduled_task_create saves a reminder"),\n'
            '                                              ("phone", "phone acts on the phone (action=open_app...)"))\n')

    replace(package / "agent.py",
            "        parts += extra_context\n"
            "        from . import lean, phone_fast\n",
            "        parts += extra_context\n"
            "        if self.depth == 0 and os.environ.get(\"TZ\") and not self._tiny():\n"
            "            from . import clock\n"
            "            parts.append(clock.note())\n"
            "        from . import lean, phone_fast\n")
