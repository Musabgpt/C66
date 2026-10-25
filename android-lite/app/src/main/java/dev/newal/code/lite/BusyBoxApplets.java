package dev.newal.code.lite;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;

/**
 * Make selected BusyBox applets transparently usable by the existing agent shell.
 * The shell's PATH already starts with our private bin; Android executes only the
 * target in nativeLibraryDir, not a copied executable in writable app data.
 *
 * Keep sh, git, python and other runtimes untouched. Android /system/bin/sh remains
 * the command interpreter, so the agent does not need to request BusyBox by name.
 */
final class BusyBoxApplets {
    static final String[] NAMES = {
        "busybox", "awk", "basename", "cat", "cp", "cut", "dirname", "env",
        "find", "grep", "head", "ls", "mkdir", "mv", "readlink", "realpath",
        "rm", "sed", "sleep", "sort", "tail", "touch", "tr", "uname",
        "wc", "xargs"
    };

    private BusyBoxApplets() { }

    static void install(File bin, File nativeLibrary) throws IOException {
        if (!nativeLibrary.isFile()) {
            return; // Builds without BusyBox keep Android's existing shell utilities.
        }
        if (!bin.isDirectory() && !bin.mkdirs()) {
            throw new IOException("Cannot create MusabAI private bin directory: " + bin);
        }
        Path target = nativeLibrary.toPath().toAbsolutePath();
        for (String name : NAMES) {
            Path link = new File(bin, name).toPath();
            if (Files.isSymbolicLink(link) && target.equals(Files.readSymbolicLink(link))) {
                continue; // Safe to run on every start, including a restored installation.
            }
            if (Files.exists(link, LinkOption.NOFOLLOW_LINKS) && !Files.isSymbolicLink(link)) {
                continue; // Never overwrite a real existing user/program file.
            }
            Files.deleteIfExists(link); // Remove only an old symlink, never a real file.
            Files.createSymbolicLink(link, target);
        }
    }

    static boolean ready(File bin, File nativeLibrary) {
        Path link = new File(bin, "busybox").toPath();
        try {
            return nativeLibrary.isFile() && Files.isSymbolicLink(link)
                && nativeLibrary.toPath().toAbsolutePath().equals(Files.readSymbolicLink(link));
        } catch (IOException e) {
            return false;
        }
    }
}
