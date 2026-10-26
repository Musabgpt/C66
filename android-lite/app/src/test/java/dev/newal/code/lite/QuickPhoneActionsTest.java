package dev.newal.code.lite;

import org.junit.Test;
import static org.junit.Assert.*;

/** Fast native phone actions must never swallow complex multi-step requests. */
public class QuickPhoneActionsTest {
    @Test public void arabicWhatsappOpensNatively() {
        assertEquals("com.whatsapp", QuickPhoneActions.packageToOpen("افتح واتساب"));
        assertEquals("com.whatsapp", QuickPhoneActions.packageToOpen("افتح الواتساب"));
        assertEquals("com.whatsapp", QuickPhoneActions.packageToOpen("افتح واتس اب"));
        assertEquals("com.whatsapp", QuickPhoneActions.packageToOpen("Open WhatsApp"));
    }

    @Test public void opensInstalledTools() {
        assertEquals("com.android.chrome", QuickPhoneActions.packageToOpen("افتح كروم"));
        assertEquals("com.termux", QuickPhoneActions.packageToOpen("شغل تيرمكس"));
        assertEquals("com.android.settings", QuickPhoneActions.packageToOpen("افتح الإعدادات"));
    }

    @Test public void neverTreatsSendingOrWritingAsSimpleLaunch() {
        assertNull(QuickPhoneActions.packageToOpen("افتح واتساب وابعث رسالة لمحمد"));
        assertNull(QuickPhoneActions.packageToOpen("أرسل رسالة على واتساب"));
        assertNull(QuickPhoneActions.packageToOpen("افتح كروم وعبّي الاستمارة"));
        assertNull(QuickPhoneActions.packageToOpen("Hi"));
        assertNull(QuickPhoneActions.packageToOpen(null));
    }
}
