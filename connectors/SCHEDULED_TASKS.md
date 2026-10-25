# MusabAI scheduled tasks (Android)

The **المهام المجدولة** page creates, lists, pauses, resumes, runs immediately, and removes persistent tasks. MusabAI also has scheduled_task_create, scheduled_task_list, scheduled_task_pause, scheduled_task_resume and scheduled_task_delete agent tools for conversational use.

## Types

- Reminder: post a native phone notification; no AI model required.
- Agent: at due time, run a new READ-ONLY model agent check and notify its result. The model and any needed connection must be available.
- Watch: recurring read-only model check; no notification on exactly NO_CHANGE. AI checks may give false alerts, so verify important claims yourself.

## Timings and background limitations

- Use timezone-aware ISO-8601 first-run date (when) or relative delay_minutes.
- Recurrence is repeat_minutes (60 to 525600), not an exact calendar schedule. Every 1440 minutes is a 24-hour interval; it may shift across daylight saving transitions.
- The scheduler wakes approximately every ten seconds while MusabAI's existing Android foreground Python service is alive. Android may delay, kill, force-stop or restrict background execution; this is NOT a guaranteed exact-time alarm. The first version does not use AlarmManager or cloud infrastructure.
- If the service stops, task state stays in the app-private SQLite file. Bring the app back to resume. Do not rely on this for critical medical or safety reminders.
- Overdue repeat periods are skipped (no catch-up burst). Notifications use the existing authenticated native Android phone API and require notification permission.
- AI tasks are run serially with read-only permissions: they cannot edit, send, install, or delete unattended. They use the configured model, not a guaranteed cloud agent.

## Examples to say in MusabAI

- ذكّرني بعد 15 دقيقة أشرب ماء
- كل ساعة افحص GitHub وخبرني إذا فشل بناء C66
- ورجيني المهام المجدولة
- أوقف المهمة الفلانية

## Implementation

- connectors/scheduled_tasks.py: SQLite persistence, interval evaluation, tools, limited execution, authenticated local API.
- connectors/scheduled_tasks_ui.js and scheduled_tasks.css: responsive task list and controls inside the app.
- connectors/apply_scheduled_tasks.py: install into the pinned Python engine without changing its upstream repository.
- connectors/scheduled_tasks_tests.py: unit and integration tests.
- Android workflow: Python tests, JS syntax check, original regression suite and development-signed APK.

Future work could add AlarmManager and boot recovery for stronger offline reminders under Android Doze constraints; these are not claimed here.
