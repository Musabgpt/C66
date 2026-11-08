package dev.newal.code.lite;

import android.view.KeyEvent;

import org.junit.Test;

import java.util.Arrays;
import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public class OpenClawNodeTest {
    private static final List<String[]> SAMPLE_CONTACTS = Arrays.asList(
            new String[] {"أحمد العتيبي", "+966501112233", "Mobile"},
            new String[] {"مروة السعيد", "+966504445566", "Mobile"},
            new String[] {"مصطفى علي", "+966507778899", "Work"},
            new String[] {"أحمد العتيبي", "+966 50 111 2233", "Mobile"},
            new String[] {"OpenClaw Support", "+14155550199", "Work"}
    );

    @Test
    public void filterContactRowsMatchesArabicFoldedAndDeduplicates() {
        List<String> ahmad = OpenClawNode.filterContactRows(SAMPLE_CONTACTS, "احمد", 10);
        assertEquals(1, ahmad.size());
        assertTrue(ahmad.get(0).contains("+966501112233"));

        List<String> marwa = OpenClawNode.filterContactRows(SAMPLE_CONTACTS, "مروه", 10);
        assertEquals(1, marwa.size());
        assertTrue(marwa.get(0).contains("+966504445566"));

        List<String> mustafa = OpenClawNode.filterContactRows(SAMPLE_CONTACTS, "مصطفي", 10);
        assertEquals(1, mustafa.size());
        assertTrue(mustafa.get(0).contains("+966507778899"));
    }

    @Test
    public void bestNumberForNameResolvesFoldedArabicAndPassesPhoneNumbersThrough() {
        assertEquals("+966501112233", OpenClawNode.bestNumberForName(SAMPLE_CONTACTS, "احمد العتيبي"));
        assertEquals("+966504445566", OpenClawNode.bestNumberForName(SAMPLE_CONTACTS, "مروه"));
        assertEquals("+14155550199", OpenClawNode.bestNumberForName(SAMPLE_CONTACTS, "openclaw"));
        assertEquals("+966599999999", OpenClawNode.bestNumberForName(SAMPLE_CONTACTS, "+966599999999"));
    }

    @Test
    public void mediaKeyCodeMapsEnglishAndArabicCommands() {
        assertEquals(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, OpenClawNode.mediaKeyCode("play_pause"));
        assertEquals(KeyEvent.KEYCODE_MEDIA_PLAY, OpenClawNode.mediaKeyCode("play"));
        assertEquals(KeyEvent.KEYCODE_MEDIA_PAUSE, OpenClawNode.mediaKeyCode("pause"));
        assertEquals(KeyEvent.KEYCODE_MEDIA_NEXT, OpenClawNode.mediaKeyCode("next"));
        assertEquals(KeyEvent.KEYCODE_MEDIA_NEXT, OpenClawNode.mediaKeyCode("التالي"));
        assertEquals(KeyEvent.KEYCODE_MEDIA_PREVIOUS, OpenClawNode.mediaKeyCode("السابق"));
        assertEquals(KeyEvent.KEYCODE_MEDIA_STOP, OpenClawNode.mediaKeyCode("إيقاف"));
        assertEquals(KeyEvent.KEYCODE_VOLUME_MUTE, OpenClawNode.mediaKeyCode("كتم"));
    }
}
