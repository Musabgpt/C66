"""The phone hits what the model meant (see the research doc: grounding is the agents' weakest point).

* A tap on an item that changed since the screen was read (a list loaded, scrolled or reordered, a dialog came) no
  longer lands blind where the item used to be, or on the row that shows something else there now: the item is found
  again by its words on the screen as it is now (the nearest one when several say the same), and when it is gone the
  tap is refused with "read the screen again". A number that ticked on it (a clock, a counter) is still the same item.
  Typing into a field whose node went away finds the field again the same way.
* Names and texts are compared the way people type them (TextFold.java, phone_fast._norm): "الاعدادات" opens the app
  called "الإعدادات", "ارسال" taps "إرسال" (أ إ آ ٱ / ا, ة / ه, ى / ي, diacritics, tatweel).
"""
import shutil

TAP_BEFORE = """\
        AccessibilityNodeInfo n = items.get(idx);
        String what = "[" + (idx + 1) + "] " + words(n);
        boolean fresh = n.refresh();
        AccessibilityNodeInfo target = fresh ? clickableParent(n) : null;
        if (target != null && target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            return settle("tapped " + what);
        }
        Rect r = places.get(idx);
        gesture(r.centerX(), r.centerY(), r.centerX(), r.centerY(), 60);
"""

TAP_AFTER = """\
        AccessibilityNodeInfo n = items.get(idx);
        String label = words(n);                        // what the model read there
        String what = "[" + (idx + 1) + "] " + label;
        Rect at = places.get(idx);
        boolean fresh = n.refresh();
        String now = fresh ? words(n) : null;
        if (now == null || !TextFold.fold(now).equals(TextFold.fold(label))) {
            // The screen changed since it was read (a list loaded, scrolled or reordered, a dialog came): the item is
            // found again by its words on the screen as it is now, never tapped blind where it used to be, or on the
            // row that shows something else there now.
            int again = findAgain(label, at, false);
            if (again >= 0) {
                n = items.get(again);
                what = "[" + (again + 1) + "] " + label + " (the screen had changed: found again at its new place)";
                at = places.get(again);
                fresh = n.refresh();
            } else if (now == null || TextFold.withoutNumbers(label).isEmpty()
                    || !TextFold.withoutNumbers(label).equals(TextFold.withoutNumbers(now))) {
                throw new IllegalStateException("the screen changed: " + what + " is not on it any more"
                        + (now == null || now.isEmpty() ? "" : " (that place says \\"" + now + "\\" now)")
                        + ". Read the screen again and tap from the new list");
            }
            // else only a number on it changed (a clock, a counter): the same item, tapped as it is now
        }
        AccessibilityNodeInfo target = fresh ? clickableParent(n) : null;
        if (target != null && target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            return settle("tapped " + what);
        }
        Rect r = at;
        if (fresh) {
            Rect where = new Rect();
            n.getBoundsInScreen(where);                 // where it is now, if it moved since the screen was read
            if (where.width() > 0 && where.height() > 0) {
                r = where;
            }
        }
        gesture(r.centerX(), r.centerY(), r.centerX(), r.centerY(), 60);
"""

TYPE_BEFORE = """\
        if (a.optInt("item", 0) > 0 && a.optInt("item") <= items.size()) {
            target = items.get(a.optInt("item") - 1);
            target.refresh();
        }
"""

TYPE_AFTER = """\
        if (a.optInt("item", 0) > 0 && a.optInt("item") <= items.size()) {
            int idx = a.optInt("item") - 1;
            target = items.get(idx);
            if (!target.refresh()) {
                // the screen changed since it was read: the field is found again, never typed into another one
                int again = findAgain(words(target), places.get(idx), true);
                if (again < 0) {
                    throw new IllegalStateException("the screen changed: field [" + (idx + 1)
                            + "] is not on it any more. Read the screen again");
                }
                target = items.get(again);
                target.refresh();
            }
        }
"""

HELPERS = """\
    /** After the screen changed: the index of the item with these words on the screen as it is now (a field: also
     *  the field at the same place), the one nearest to where it was when several match; -1 when it is gone. */
    private int findAgain(String label, Rect was, boolean field) throws Exception {
        screen();
        String want = TextFold.fold(label);
        int found = -1;
        long best = Long.MAX_VALUE;
        for (int i = 0; i < items.size(); i++) {
            AccessibilityNodeInfo m = items.get(i);
            Rect r = places.get(i);
            String w = TextFold.fold(words(m));
            boolean same;
            if (field) {
                same = m.isEditable() && ((!want.isEmpty() && w.equals(want)) || r.equals(was));
            } else {
                same = want.isEmpty() ? w.isEmpty() && r.equals(was) : w.equals(want);
            }
            if (!same) {
                continue;
            }
            long d = (long) Math.abs(r.centerX() - was.centerX()) + Math.abs(r.centerY() - was.centerY());
            if (d < best) {
                best = d;
                found = i;
            }
        }
        return found;
    }

"""


def apply(src, package, replace):
    shutil.copyfile(src / "ext_grounding_tests.py", package / "ext_grounding_tests.py")

    replace(package / "phone_fast.py", "    return APP_NAMES.get(key, name)\n",
            "    hit = APP_NAMES.get(key)\n"
            "    if hit is None:\n"
            "        # the name as people type it (أ/ا, ة/ه, ى/ي, diacritics): matched like the app folds it (TextFold)\n"
            "        folded = _norm(key)\n"
            "        hit = next((v for k, v in APP_NAMES.items() if _norm(k) == folded), None)\n"
            "    return hit or name\n")

    java = package.parent.parent / "android-lite/app/src/main/java/dev/newal/code/lite"
    if java.is_dir():
        phone = java / "Phone.java"
        replace(phone, "        String want = name.trim().toLowerCase(Locale.ROOT);\n",
                "        String want = TextFold.fold(name.trim());\n")
        replace(phone, "            String label = app[0].toLowerCase(Locale.ROOT);\n",
                "            String label = TextFold.fold(app[0]);\n")

        pcs = java / "PhoneControlService.java"
        replace(pcs, '            String want = a.optString("text").trim().toLowerCase(Locale.ROOT);\n',
                '            String want = TextFold.fold(a.optString("text").trim());\n')
        replace(pcs, "                String w = words(items.get(i)).toLowerCase(Locale.ROOT);\n",
                "                String w = TextFold.fold(words(items.get(i)));\n")
        replace(pcs, TAP_BEFORE, TAP_AFTER)
        replace(pcs, TYPE_BEFORE, TYPE_AFTER)
        replace(pcs, "    synchronized JSONObject type(JSONObject a) throws Exception {\n",
                HELPERS + "    synchronized JSONObject type(JSONObject a) throws Exception {\n")

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_grounding_tests import GroundingTests  # noqa: E402,F401 - run with the parity tests in CI\n")
