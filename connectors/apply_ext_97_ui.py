"""The phone UI pass (the MusabAI prompt study, phase 4).

* One name: the interface says MusabAI everywhere it said NewAl Code (labels, toasts and their Arabic translations
  change together, so every translation still matches).
* The permission picker no longer says "Agent" next to the Agent / Plan / Ask modes: Read only, Ask first, Auto, Full
  access.
* A failed tool shows its error once (not in a grey box and again in a red one).
* ext_phone.css: top bar, tool buttons and review pane fit a phone; English text runs left to right in Arabic.
* The new buttons' labels are in the Arabic dictionary.
"""
import shutil

BRAND = (("NewAl Code Lite", "MusabAI"), ("NewAl Code", "MusabAI"))


def apply(src, package, replace):
    ui = package / "ui"
    shutil.copyfile(src / "ext_phone.css", ui / "ext_phone.css")
    replace(ui / "index.html", '<link rel="stylesheet" href="ext_fleet.css">',
            '<link rel="stylesheet" href="ext_fleet.css">\n<link rel="stylesheet" href="ext_phone.css">')

    for name in ("index.html", "app.js", "i18n.js"):
        path = ui / name
        text = path.read_text(encoding="utf-8")
        if "NewAl Code" not in text:
            raise SystemExit("Unsafe patch refused: no NewAl Code label in ui/%s" % name)
        for old, new in BRAND:
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")

    app = ui / "app.js"
    replace(app, '    ["read-only", "Chat", "Reads and explains; changes nothing"],\n',
            '    ["read-only", "Read only", "Reads and explains; changes nothing"],\n')
    replace(app, '    ["auto-edit", "Agent", "Edits the project and runs commands; asks before risky ones"],\n',
            '    ["auto-edit", "Auto", "Edits the project and runs commands; asks before risky ones"],\n')
    replace(app, '    ["full-auto", "Agent · full access", "Never asks (catastrophic commands are still refused)"],\n',
            '    ["full-auto", "Full access", "Never asks (catastrophic commands are still refused)"],\n')
    replace(app, '          if (ev.text) body.appendChild(h("pre", "out", esc(ev.text.slice(0, 4000))));\n',
            '          if (ev.text && ev.ok) body.appendChild(h("pre", "out", esc(ev.text.slice(0, 4000))));   // an error: once, below\n')

    i18n = ui / "i18n.js"
    replace(i18n, '    "Terminal": "الطرفية",\n',
            '    "Terminal": "الطرفية",\n    "Files": "الملفات",\n    "Preview": "معاينة",\n    "Agents": "الوكلاء",\n'
            '    "Plan": "خطة",\n    "Ask": "سؤال",\n    "Read only": "قراءة فقط",\n')
    text = i18n.read_text(encoding="utf-8")
    key = '    "Ask MusabAI anything. @ to add files, / for commands": '
    start = text.find(key)
    if start < 0:
        raise SystemExit("Unsafe patch refused: composer placeholder not in i18n.js")
    end = text.index("\n", start) + 1
    replace(i18n, text[start:end], key + '"اكتب طلبك هنا… (@ لإضافة ملفات، / للأوامر)",\n')
