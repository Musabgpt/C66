"""The two buttons of an empty thread, in Arabic: they were the only English words on the Arabic start screen.

i18n.js translates the text as the page draws it, from its table (the English is the key). These two keys were missing.
"""


def apply(src, package, replace):
    replace(package / "ui/i18n.js", '    "New thread": "محادثة جديدة",\n',
            '    "New thread": "محادثة جديدة",\n    "Open a project": "فتح مشروع",\n    "Download a model": "تنزيل نموذج",\n')
