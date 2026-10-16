"""The phone hits what the model meant (apply_ext_99_grounding.py): app names typed with other Arabic letter forms
open the right app, and the app's Java finds a stale item again by its words instead of tapping where it used to be."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JAVA = os.path.join(ROOT, "android-lite", "app", "src", "main", "java", "dev", "newal", "code", "lite")


class GroundingTests(unittest.TestCase):
    def test_app_names_typed_with_other_letter_forms_open_the_right_app(self):
        from . import tools, phone_fast  # noqa: F401 - tools first: it loads phone_fast while it is being imported
        self.assertEqual(phone_fast.app_name("واتسآب"), "com.whatsapp")
        self.assertEqual(phone_fast.app_name("يوتيوبْ"), "com.google.android.youtube")
        self.assertEqual(phone_fast.app_name("الأِعدادات"), "com.android.settings")
        self.assertEqual(phone_fast.app_name("تطبيق واتسآب"), "com.whatsapp")
        self.assertEqual(phone_fast.app_name("Some Unknown App"), "Some Unknown App")

    @unittest.skipUnless(os.path.isdir(JAVA), "the Android sources are not next to this engine")
    def test_the_app_folds_names_and_finds_a_stale_item_again(self):
        with open(os.path.join(JAVA, "PhoneControlService.java"), encoding="utf-8") as f:
            service = f.read()
        with open(os.path.join(JAVA, "Phone.java"), encoding="utf-8") as f:
            phone = f.read()
        self.assertIn("private int findAgain(String label, Rect was, boolean field)", service)
        self.assertIn("int again = findAgain(label, at, false);", service)          # a tap on a stale item
        self.assertIn("int again = findAgain(words(target), places.get(idx), true);", service)  # typing
        self.assertIn("n.getBoundsInScreen(where);", service)          # a gesture lands where the item is now
        self.assertIn('String want = TextFold.fold(a.optString("text").trim());', service)
        self.assertIn("String want = TextFold.fold(name.trim());", phone)
        self.assertTrue(os.path.isfile(os.path.join(JAVA, "TextFold.java")))
