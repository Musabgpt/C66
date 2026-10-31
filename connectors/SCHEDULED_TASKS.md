# MusabAI scheduled tasks (Android)

The **المهام المجدولة** page creates, lists, pauses, resumes, runs immediately, and removes persistent tasks. MusabAI also has scheduled_task_create, scheduled_task_list, scheduled_task_pause, scheduled_task_resume and scheduled_task_delete agent tools for conversational use.

## Types

- Reminder: post a native phone notification; no AI model required.
- Agent: at due time, run a new FULL-AUTO agent by default, carry out the user's requested phone/app/GitHub/code task, verify results and notify. The model and any needed connections/Android permissions must be available.
- Watch: recurring agent check; if condition false, return NO_CHANGE without taking action. If true, perform the user's requested actions, notify and mark the watch complete (prevents repeated side-effects). The agent's judgement may be wrong, so review important outcomes.
- Individual agent/watch tasks can opt into read-only explicitly using permission_mode=read-only; the default is full-auto.

## Timings and background limitations

- Use an ISO-8601 first-run date (when) or relative delay_minutes. The app passes the phone's time zone to the engine (TZ), each request tells the model the local time and UTC offset, and a time without offset is the phone's local time.
- Recurrence is repeat_minutes (60 to 525600), not an exact calendar schedule. Every 1440 minutes is a 24-hour interval; it may shift across daylight saving transitions.
- The scheduler wakes approximately every ten seconds while MusabAI's existing Android foreground Python service is alive. After every change to the tasks the engine asks the app for an exact alarm at the next due time (TaskAlarms.java, USE_EXACT_ALARM): when Android has stopped the app meanwhile, the alarm starts the engine's service and the task runs. The alarm is set again after the phone restarts or the app is updated. If Android refuses to start the service from the background, a notification lists what is due.
- A force-stopped app gets no alarms until it is opened; task state stays in the app-private SQLite file. Do not rely on this for critical medical or safety reminders.
- Overdue repeat periods are skipped (no catch-up burst). Notifications use the existing authenticated native Android phone API and require notification permission.
- AI tasks run serially in full-auto by default. This enables already-granted phone, shell, GitHub and file tools to actually execute authorized operations. Android runtime permissions and safeguards still apply; full-auto cannot bypass Accessibility, app installation approvals or system restrictions. Irreversible actions outside the user's instructions still need explicit approval. They use the configured model, not a guaranteed cloud agent.
- Older saved agent/watch tasks made by the previous read-only release are migrated to full-auto automatically in this update, as explicitly requested by the user. You can pause them or choose read-only for new tasks.
- The UI task creation form defaults to full-auto for agent jobs and offers read-only as an optional setting.

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
- android-lite/.../TaskAlarms.java and TaskAlarmReceiver.java: the exact alarm for the next due task, read from the tasks' database, and its renewal after a restart or an update.
