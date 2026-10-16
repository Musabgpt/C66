package dev.newal.code.lite;

import org.junit.Test;
import static org.junit.Assert.*;

public class EngineReadinessTest {
    @Test public void healthyResumeKeepsTheLoadedPage() {
        EngineReadiness state = new EngineReadiness();
        state.resume();
        int first = state.beginCheck();
        assertEquals(EngineReadiness.LOAD_PAGE, state.complete(first));
        state.pause();
        state.resume();
        int resumed = state.beginCheck();
        assertEquals(EngineReadiness.KEEP_PAGE, state.complete(resumed));
        assertTrue(state.hasPage());
    }

    @Test public void onlyOneCheckCanRunAndPauseCancelsItsCallback() {
        EngineReadiness state = new EngineReadiness();
        assertEquals(-1, state.beginCheck());
        state.resume();
        int first = state.beginCheck();
        assertTrue(first >= 0);
        assertEquals(-1, state.beginCheck());
        state.pause();
        assertFalse(state.current(first));
        assertEquals(EngineReadiness.IGNORE, state.complete(first));
        state.resume();
        int resumed = state.beginCheck();
        assertTrue(state.current(resumed));
        assertFalse(state.current(first));
    }

    @Test public void restartWaitsForServiceAcknowledgementAndReloadsOnce() {
        EngineReadiness state = new EngineReadiness();
        state.resume();
        state.complete(state.beginCheck());
        int staleCheck = state.beginCheck();
        int request = state.requestStart(true);
        assertFalse(state.current(staleCheck));
        int current = state.beginCheck();
        assertFalse(state.canProbe(current));
        assertEquals(EngineReadiness.IGNORE, state.complete(current));
        assertTrue(state.serviceStarted(request));
        assertTrue(state.canProbe(current));
        assertEquals(EngineReadiness.LOAD_PAGE, state.complete(current));
        assertEquals(EngineReadiness.IGNORE, state.complete(current));
    }

    @Test public void deadEngineRecoveryKeepsDraftAndIgnoresEarlierServiceAcknowledgement() {
        EngineReadiness state = new EngineReadiness();
        state.resume();
        state.complete(state.beginCheck());
        int previous = state.requestStart(false);
        int recovery = state.requestStart(false);
        int check = state.beginCheck();
        assertFalse(state.serviceStarted(previous));
        assertFalse(state.canProbe(check));
        assertTrue(state.serviceStarted(recovery));
        assertEquals(EngineReadiness.KEEP_PAGE, state.complete(check));
    }

    @Test public void failedRecoveryKeepsLoadedPageAndCanBeRetriedOnResume() {
        EngineReadiness state = new EngineReadiness();
        state.resume();
        state.complete(state.beginCheck());
        int failed = state.beginCheck();
        assertTrue(state.failed(failed));
        assertFalse(state.current(failed));
        assertTrue(state.hasPage());
        state.pause();
        state.resume();
        assertTrue(state.beginCheck() >= 0);
    }

    @Test public void serviceCompletionWhilePausedCannotOpenThePage() {
        EngineReadiness state = new EngineReadiness();
        state.resume();
        int request = state.requestStart(true);
        int check = state.beginCheck();
        state.pause();
        state.serviceStarted(request);
        assertEquals(-1, state.beginCheck());
        assertEquals(EngineReadiness.IGNORE, state.complete(check));
        state.resume();
        assertEquals(EngineReadiness.LOAD_PAGE, state.complete(state.beginCheck()));
    }

    @Test public void failedServiceCannotAcceptHealthFromThePreviousProcess() {
        EngineReadiness state = new EngineReadiness();
        state.resume();
        state.complete(state.beginCheck());
        int request = state.requestStart(true);
        int check = state.beginCheck();
        assertTrue(state.serviceStarted(request, "Previous engine did not stop"));
        assertFalse(state.canProbe(check));
        assertEquals(EngineReadiness.IGNORE, state.complete(check));
        assertEquals("Previous engine did not stop", state.startupError());
        int retry = state.requestStart(true);
        assertEquals("", state.startupError());
        assertFalse(state.serviceStarted(request, "stale failure"));
        assertTrue(state.serviceStarted(retry));
        assertEquals(EngineReadiness.LOAD_PAGE, state.complete(state.beginCheck()));
    }
}
