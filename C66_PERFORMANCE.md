# C66 performance changes

## Assistant speed and background tasks (2026-10-31)

Measured on the phone itself (Galaxy A16, Android 16) with the app's own Python 3.14, running the engine from build 84
and from this change side by side in a temporary folder.

| What | Before | After |
| --- | --- | --- |
| A real model call (anonymous Kilo gateway, 5 calls, two rounds) | median 1054 / 1046 ms | median 834 / 839 ms |
| Engine ready after an install or update (first start) | 1.8-2.6 s | 0.38 s |
| Floating assistant noticing the answer | up to 750 ms after it, reading the whole thread each time | as soon as the turn ends, only the new events |

- **Connections reused** (connectors/http_pool.py, apply_ext_99_zspeed.py). Every model call built a TLS context and
  read the CA list (88 ms) and opened a new connection (DNS, TCP and TLS, 130-170 ms). Now one context is kept and a
  response read to its end leaves its connection for the next call to the same host. A connection the server has
  closed is dropped or, if it closed as the request went out, the request is sent once more on a new one.
- **Bytecode shipped** (connectors/ship_bytecode.py, hot_stdlib.txt). Checked-hash .pyc for the whole engine and for
  the 128 standard-library modules a start and a first request import, made with Python 3.14 in CI. The APK grows by
  about 2 MB.
- **Floating assistant follows a turn through `GET /api/sessions/<id>/events?after=N&wait=S`**
  (Service.wait_events, apply_ext_99_zz_assistant.py). It used to read up to 1500 events every 750 ms. On a long
  thread a read passed 500 KB and failed, and the assistant then said it could not reach the engine. After 30 idle
  minutes the assistant also opens a fresh thread, so the model reads a short conversation.
- **Honest failures.** A turn that ended in an engine error was announced by the bubble as "finished successfully".
  The error now reaches the thread and is spoken.
- **Local time.** Python on Android has no time zone and counted in UTC. The app now passes the phone's zone (TZ), each
  request carries "Local time: ... (UTC+hh:mm, zone)", and a reminder time without offset is local time.
- **Scheduled tasks run when Android has stopped the app.** An exact alarm for the next due task starts the engine
  (TaskAlarms.java), and it is set again after a restart or an update. Before, tasks waited until the app was opened.
- **web_search answers the question asked** (connectors/websearch.py). Bing's feed, tried first, answered
  "سعر الذهب اليوم في الأردن" with Chinese pages about SoundCloud (and Lidl's UK stores), and "weather Amman today"
  with the weather in Derby. Because it returned something, DuckDuckGo was never asked. Now DuckDuckGo goes first and a
  list is taken only when its results carry the query's words (Arabic letter forms folded, the article dropped).
  Live on the phone, the gold question now gets three Jordanian gold-price pages.
- **A weather tool** (connectors/weather.py, Open-Meteo, no key) gives the numbers for now, today and two more days,
  in about 0.5 s. With no place it uses the phone's last known approximate position, once the user allows it from the
  home screen card (Phone action "location", no GPS fix, rounded to about a kilometre). Otherwise it uses the time
  zone's city and says so: a phone in Birmingham is on Europe/London. "عمان" without its shadda finds only villages in
  Iraq and Yemen at the geocoder, so the phone's own city wins when its name without marks matches.
- **Claims without action are caught.** The free model answered two of four reminder requests with "تم. سأذكرك..."
  or "**تم الحفظ**" and saved nothing. The engine's check for such replies missed "تم" next to a full stop or an
  asterisk, and its correction named no tool that saves a reminder. Both are fixed; in the next four live requests
  all four reminders were saved, at the right local time.

Live turns on the phone with the app's default free model (old engine without a time zone, as the old app ran it,
against the new one with a phone zone of Asia/Amman to show a zone other than UTC):

| Request | Old engine | New engine |
| --- | --- | --- |
| "شو الطقس اليوم؟" | 63 s, 20 searches and fetches, a vague answer about London | 7 s, one weather call, the numbers |
| "ذكرني بكرا الساعة 9 الصبح آخذ الدوا" | saved for 12:00 local time | saved for 09:00 local time |
| "كم سعر الذهب اليوم في الأردن؟" | 89 s, no price | 58 s, still no price (the pages' snippets carry none) |

Tests: connectors/http_pool_tests.py (9), assistant_events_tests.py (11), websearch_tests.py (11),
weather_tests.py (8), and new cases in scheduled_tasks_tests.py. On the phone, the CI list of engine suites shows the
same 16 environment failures before and after this change (no git, bash or Termux in the temporary folder), and the
same 73 unclosed-resource warnings under `-X dev`.

## Earlier changes

Source: Musabgpt/NewAl-Cloud at `4e8fd61a133502e7d339f9d9404585168451bd15`.

## Changes

- Connections maintenance now ignores streamed chat updates. It still handles inserted dialogs, refreshed connector cards and language changes. Previously every chat DOM mutation scheduled a full connections tidy pass.
- Provider fallback selection checks capability requirements and cooldowns before fetching Android vault credentials. Previously excluded providers still incurred phone bridge requests (each bridge request has a 30-second timeout).
- Android CI accepts `main` and `c66-performance` so the new repository's branch can build using the existing pinned native baseline. No signing key is included. Without the release secret, the existing workflow builds a development APK.

## Evidence (2026-10-23, Linux)

- `node connectors/benchmark_lean.mjs`: 100 simulated chat updates schedule **0** connector maintenance frames, versus **100** with the original source. This measures eliminated work, not overall response latency or physical-phone frame rate.
- `npm test --prefix connectors`: **83 passed**, 0 failed.
- Assembled pinned Python engine (`0bf36a3b3a813dbac424ee0c4dc6341f9e3fe0d3`) with autonomy and connector/parity patches, then ran `provider_pool_tests`, `provider_keys_tests`, `claude_parity_tests`: **134 tests, OK, 3 skipped**. An existing ResourceWarning for an unclosed reader in `service.py` was emitted.
- Signing/update publication tests: **8 passed**.
- Regression demonstrated before the fix: two excluded providers still read credentials. After the fix: no credential reads for those routes.
- New UI regression tests failed on the original implementation (unnecessary scheduled work and missing language-change refresh) and pass after the change.
- `git diff --check`: clean.

The first temporary engine assembly lacked the Android source/assets layout and could not run the lean module tests. The harness was rebuilt with the current Android sources; full connector/parity patch application then succeeded. No APK build or installation of these changes has been performed. Live provider response time remains dependent on the selected provider, network and request size.

## Reproduce UI benchmark

```sh
npm ci --prefix connectors --ignore-scripts --no-audit --no-fund
git show 4e8fd61a133502e7d339f9d9404585168451bd15:connectors/lean_ui.js > /tmp/c66-original-lean.js
node connectors/benchmark_lean.mjs /tmp/c66-original-lean.js
node connectors/benchmark_lean.mjs
npm test --prefix connectors
```

The original remote is named `upstream`; changes are on `main`. Publishing to `Musabgpt/C66` requires GitHub authentication with repository creation access. The original repository has not been modified.
