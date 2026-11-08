# Task memory (ذاكرة المهام)

The library of procedures the assistant uses on the phone and the web, built from four sources into one SQLite
database that the APK ships (`android-lite/app/src/main/assets/task_memory/library.db.gzip`; not `.gz`, which the
Android build would un-gzip and rename on its own).

| Source | What it gives | License | In the library |
|---|---|---|---|
| Curated (`curated.py`) | Hand-written steps for Facebook, WhatsApp, Instagram, Telegram, X, Gmail, YouTube, Play Store, settings… with button labels in English and Arabic | MIT | 57, all runnable |
| [AndroidControl](https://github.com/google-research/google-research/tree/master/android_control) | 15,273 real Android demonstrations: goal, every step's instruction, and what each tap hit (read from the screen's accessibility tree) | Apache-2.0 | 2,699 runnable, the rest guidance |
| [Mind2Web](https://osu-nlp-group.github.io/Mind2Web/) (train split only) | 1,009 website tasks with every click, typed text and choice | CC BY 4.0 | 676 runnable in Chrome |
| [InSTA-150K](https://data-for-agents.github.io) | 146,746 website goals (site + task) | MIT | guidance |

Every procedure is sorted into one of 21 categories (social, messaging, shopping, travel, media…) and has an app or a
site, its steps (`open_app`, `open_url`, `tap` with every label it may have, `type`, `key`, `scroll`, `phone`…), and
`{1}`, `{2}` blanks for the values a request gives (a post's text, a contact). The Mind2Web test splits are not used:
their authors keep them password-protected so they stay out of training data.

## How the app uses it

* `TaskMemory.java` matches a request ("افتح فيسبوك وانشر منشور يقول صباح الخير") to the procedure that does it, with
  the values it gives (`صباح الخير`), in Arabic or English (an Arabic-English word list bridges the two).
* The agent gets the matching procedure under a phone request (`connectors/task_memory.py`, through
  `phone_fast.note`) and runs it with `phone_routine id=tm:...`; `TaskRunner.java` carries the steps out, trying each
  label and stopping where the screen differs so the agent continues by hand.
* **Task Memory** has its own launcher icon (`TaskMemoryActivity`): categories, sources, search, "try a request",
  procedure details and a Run button.

## Rebuilding

```bash
pip install pyarrow
# AndroidControl: the parsed steps and the per-step screens (public Hugging Face mirrors of the official TFRecords)
curl -LO https://huggingface.co/datasets/HarrytheOrange/parsed_AndroidControl/resolve/main/parsed_android_control.jsonl
# + the 7 parquet files of ckg/AndroidControlParsed-20k into ac/
python3 convert_androidcontrol.py ac/ parsed_android_control.jsonl androidcontrol.jsonl
# Mind2Web train: action_reprs per task (osunlp/Mind2Web) as compact JSON lines, then
python3 convert_mind2web.py mind2web_compact.jsonl mind2web.jsonl
# InSTA-150K: data-for-agents/insta-150k-v1 parquet files
python3 convert_insta.py insta150k.jsonl train.parquet test.parquet
python3 build_db.py --out library.db --androidcontrol androidcontrol.jsonl --mind2web mind2web.jsonl \
    --insta insta150k.jsonl --gzip ../../android-lite/app/src/main/assets/task_memory/library.db.gzip
```

`textnorm.py` and `TaskMemoryText.java` must make the same search terms: `task_memory_tests.py` and
`TaskMemoryTextTest.java` check the same examples.
