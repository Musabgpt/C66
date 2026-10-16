package dev.newal.code.lite;

/** Lifecycle state shared by the activity and its cancellable health check. */
final class EngineReadiness {
    static final int IGNORE = -1, KEEP_PAGE = 0, LOAD_PAGE = 1;
    private boolean resumed, pageLoaded, replacePage;
    private int generation, activeCheck = -1, startSequence, pendingStart = -1;
    private String startupError = "";

    synchronized void resume() { resumed = true; }

    synchronized void pause() {
        resumed = false;
        activeCheck = -1;
    }

    synchronized int beginCheck() {
        if (!resumed || activeCheck != -1) return -1;
        return activeCheck = ++generation;
    }

    synchronized boolean current(int check) {
        return resumed && check >= 0 && check == activeCheck;
    }

    synchronized int requestStart(boolean replace) {
        activeCheck = -1;
        replacePage |= replace;
        startupError = "";
        return pendingStart = ++startSequence;
    }

    synchronized boolean serviceStarted(int request) {
        return serviceStarted(request, "");
    }

    synchronized boolean serviceStarted(int request, String error) {
        if (request < 0 || request != pendingStart) return false;
        startupError = error;
        pendingStart = -1;
        return true;
    }

    synchronized boolean servicePending() { return pendingStart != -1; }

    synchronized String startupError() { return startupError; }

    synchronized boolean canProbe(int check) {
        return current(check) && !servicePending() && startupError.isEmpty();
    }

    synchronized int complete(int check) {
        if (!canProbe(check)) return IGNORE;
        activeCheck = -1;
        int result = !pageLoaded || replacePage ? LOAD_PAGE : KEEP_PAGE;
        pageLoaded = true;
        replacePage = false;
        return result;
    }

    synchronized boolean failed(int check) {
        if (!current(check)) return false;
        activeCheck = -1;
        return true;
    }

    synchronized boolean hasPage() { return pageLoaded && !replacePage; }
}
