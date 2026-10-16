package dev.newal.code.lite;

import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;

/**
 * Native/stable gate for Python revision activation.
 *
 * Python can stage and verify candidates, but it cannot declare itself healthy. This
 * class validates the saved evidence independently before PYTHONPATH changes, marks a
 * pending revision healthy only after Android observes the real localhost health
 * endpoint, and can atomically restore the last healthy pointer after startup failure.
 */
final class CandidateSelection {
    private CandidateSelection() { }

    static File select(File home, String packagedBuild) {
        return select(home, packagedBuild, Setup.UPDATE_COMPAT);
    }

    /** Invalid, stale or unreadable selections always leave the packaged engine selected. */
    static File select(File home, String packagedBuild, String compatibility) {
        Selection selection = validated(home, packagedBuild, compatibility);
        return selection == null ? null : selection.candidate;
    }

    /** Confirm a pending revision only after MainActivity has observed a real healthy server. */
    static boolean markHealthy(File home, String packagedBuild, String compatibility) {
        try {
            Selection selection = validated(home, packagedBuild, compatibility);
            if (selection == null || !"activating".equals(selection.active.optString("state", ""))) return false;
            selection.active.put("state", "active");
            selection.active.put("health_confirmed", true);
            selection.active.put("healthy_at", System.currentTimeMillis() / 1000.0);
            atomicJson(selection.activeFile, selection.active);
            Files.deleteIfExists(selection.evolution.resolve("last-failure.json"));
            return true;
        } catch (Exception ignored) {
            return false;
        }
    }

    /**
     * Roll back only a pending activation. A healthy active revision is never removed
     * by a transient later failure. The previous pointer is independently validated
     * before it is restored; otherwise packaged code is selected.
     */
    static boolean rollbackPending(File home, String packagedBuild, String compatibility, String reason) {
        if (home == null) return false;
        try {
            Path evolution = home.getCanonicalFile().toPath().resolve(".newal-code/evolution");
            Path activeFile = evolution.resolve("active.json");
            JSONObject active = readJson(activeFile);
            if (!"activating".equals(active.optString("state", ""))) return false;
            String failed = string(active, "id");
            JSONObject previous = active.opt("previous") instanceof JSONObject
                    ? active.getJSONObject("previous") : null;
            boolean restored = false;
            if (previous != null && validateMarker(home, packagedBuild, compatibility, previous) != null) {
                atomicJson(activeFile, previous);
                restored = true;
            } else {
                Files.deleteIfExists(activeFile);
            }
            JSONObject failure = new JSONObject()
                    .put("state", "rolled_back")
                    .put("cause", "failed_activation")
                    .put("failed_revision", failed == null ? "" : failed)
                    .put("restored_revision", restored ? previous.optString("id", "") : "")
                    .put("error", reason == null ? "activation health failed" : reason)
                    .put("rolled_back_at", System.currentTimeMillis() / 1000.0);
            atomicJson(evolution.resolve("last-failure.json"), failure);
            return true;
        } catch (Exception ignored) {
            return false;
        }
    }

    private static Selection validated(File home, String packagedBuild, String compatibility) {
        if (home == null || packagedBuild == null || packagedBuild.isEmpty()) return null;
        try {
            if (Files.isSymbolicLink(home.toPath())) return null;
            Path root = home.getCanonicalFile().toPath();
            directory(root);
            Path config = root.resolve(".newal-code");
            directory(config);
            Path evolution = config.resolve("evolution");
            directory(evolution);
            Path activeFile = evolution.resolve("active.json");
            JSONObject active = readJson(activeFile);
            File candidate = validateMarker(home, packagedBuild, compatibility, active);
            return candidate == null ? null : new Selection(candidate, evolution, activeFile, active);
        } catch (Exception ignored) {
            return null;
        }
    }

    /** Validate both the pointer and immutable record/digest it references. */
    private static File validateMarker(File home, String packagedBuild, String compatibility, JSONObject active)
            throws Exception {
        String id = string(active, "id");
        if (id == null || !id.matches("[0-9a-f]{24}")) return null;
        String state = string(active, "state");
        if (state != null && !"active".equals(state) && !"activating".equals(state)) return null;

        Path root = home.getCanonicalFile().toPath();
        Path evolution = root.resolve(".newal-code/evolution");
        Path candidates = evolution.resolve("candidates");
        directory(candidates);
        Path candidate = candidates.resolve(id);
        directory(candidate);
        try (java.nio.file.DirectoryStream<Path> entries = Files.newDirectoryStream(candidate)) {
            for (Path entry : entries) {
                String name = entry.getFileName().toString();
                if (!"newal_code".equals(name) && !"test-home".equals(name)) return null;
                directory(entry);
            }
        }

        File expected = new File(home, ".newal-code/evolution/candidates/" + id);
        if (!expected.getAbsolutePath().equals(string(active, "path"))
                || !candidate.toFile().equals(expected.getCanonicalFile())) return null;

        JSONObject record = readJson(evolution.resolve(id + ".json"));
        String verifiedDigest = string(record, "verified_digest");
        String kind = string(record, "kind");
        String recordStatus = string(record, "status");
        if (!id.equals(string(record, "id"))
                || (!"verified".equals(recordStatus) && !"ready_to_activate".equals(recordStatus))
                || verifiedDigest == null || !verifiedDigest.matches("[0-9a-f]{64}")) return null;

        if ("update".equals(kind)) {
            if (compatibility == null || compatibility.isEmpty()
                    || !compatibility.equals(string(active, "compatibility_id"))
                    || !compatibility.equals(string(record, "compatibility_id"))) return null;
            Integer version = integer(record, "version_code");
            Integer markerVersion = integer(active, "version_code");
            Integer packaged = integer(packagedBuild);
            if (version == null || markerVersion == null || !version.equals(markerVersion)) return null;
            if (packaged != null && version <= packaged) {
                if (!active.optBoolean("downgrade_allowed", false)
                        || !record.optBoolean("downgrade_allowed", false)) return null;
            }
        } else {
            if (!packagedBuild.equals(string(active, "packaged_build"))
                    || !packagedBuild.equals(string(record, "packaged_build"))) return null;
        }

        Path engine = candidate.resolve("newal_code");
        directory(engine);
        regularFile(engine.resolve("__init__.py"));
        regularFile(engine.resolve("server.py"));
        List<Path> files = new ArrayList<>();
        Files.walkFileTree(candidate, new SimpleFileVisitor<Path>() {
            @Override public FileVisitResult preVisitDirectory(Path dir, BasicFileAttributes attrs)
                    throws IOException {
                if (attrs.isSymbolicLink() || !attrs.isDirectory()) throw new IOException("Unsafe directory");
                return FileVisitResult.CONTINUE;
            }

            @Override public FileVisitResult visitFile(Path file, BasicFileAttributes attrs)
                    throws IOException {
                if (attrs.isSymbolicLink() || !attrs.isRegularFile()) throw new IOException("Unsafe file");
                if (file.startsWith(engine) && !ignored(engine.relativize(file))) files.add(file);
                return FileVisitResult.CONTINUE;
            }
        });

        files.sort((left, right) -> comparePaths(engine.relativize(left), engine.relativize(right)));
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[32768];
        for (Path file : files) {
            String relative = engine.relativize(file).toString().replace(File.separatorChar, '/');
            digest.update(relative.getBytes(StandardCharsets.UTF_8));
            try (InputStream input = Files.newInputStream(file, LinkOption.NOFOLLOW_LINKS)) {
                for (int count; (count = input.read(buffer)) != -1; ) digest.update(buffer, 0, count);
            }
        }
        StringBuilder actual = new StringBuilder(64);
        for (byte value : digest.digest()) {
            actual.append(Character.forDigit((value & 255) >>> 4, 16));
            actual.append(Character.forDigit(value & 15, 16));
        }
        return verifiedDigest.equals(actual.toString()) ? candidate.toFile() : null;
    }

    private static JSONObject readJson(Path path) throws Exception {
        regularFile(path);
        Object value = new JSONTokener(new String(Files.readAllBytes(path), StandardCharsets.UTF_8)).nextValue();
        if (!(value instanceof JSONObject)) throw new IOException("JSON object required");
        return (JSONObject) value;
    }

    private static void atomicJson(Path path, JSONObject object) throws Exception {
        Files.createDirectories(path.getParent());
        Path tmp = path.resolveSibling(path.getFileName() + ".tmp-" + Long.toHexString(System.nanoTime()));
        try {
            byte[] data = object.toString().getBytes(StandardCharsets.UTF_8);
            Files.write(tmp, data);
            try {
                Files.move(tmp, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            } catch (AtomicMoveNotSupportedException e) {
                Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING);
            }
        } finally {
            Files.deleteIfExists(tmp);
        }
    }

    private static String string(JSONObject object, String key) {
        Object value = object.opt(key);
        return value instanceof String ? (String) value : null;
    }

    private static Integer integer(JSONObject object, String key) {
        Object value = object.opt(key);
        return value instanceof Number ? ((Number) value).intValue() : null;
    }

    private static Integer integer(String value) {
        try { return Integer.valueOf(value); }
        catch (Exception ignored) { return null; }
    }

    private static void directory(Path path) throws IOException {
        if (!Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS)) throw new IOException("Missing directory");
    }

    private static void regularFile(Path path) throws IOException {
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) throw new IOException("Missing file");
    }

    private static boolean ignored(Path path) {
        for (Path part : path) {
            if ("__pycache__".equals(part.toString()) || ".git".equals(part.toString())) return true;
        }
        return false;
    }

    private static int comparePaths(Path left, Path right) {
        for (int i = 0; i < Math.min(left.getNameCount(), right.getNameCount()); i++) {
            int order = compareCodePoints(left.getName(i).toString(), right.getName(i).toString());
            if (order != 0) return order;
        }
        return Integer.compare(left.getNameCount(), right.getNameCount());
    }

    private static int compareCodePoints(String left, String right) {
        int l = 0, r = 0;
        while (l < left.length() && r < right.length()) {
            int a = left.codePointAt(l), b = right.codePointAt(r);
            if (a != b) return Integer.compare(a, b);
            l += Character.charCount(a);
            r += Character.charCount(b);
        }
        return Integer.compare(left.length() - l, right.length() - r);
    }

    private static final class Selection {
        final File candidate;
        final Path evolution, activeFile;
        final JSONObject active;

        Selection(File candidate, Path evolution, Path activeFile, JSONObject active) {
            this.candidate = candidate;
            this.evolution = evolution;
            this.activeFile = activeFile;
            this.active = active;
        }
    }
}
