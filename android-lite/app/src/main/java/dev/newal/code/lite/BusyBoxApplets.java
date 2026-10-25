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
    // Only applets compiled into the pinned Android native binary are linked.
    // Preserve the Android shell, system Toybox and Termux's own executables.
    static final String[] NAMES = {
        "busybox", "awk", "basename", "cat", "cp", "cut", "dirname",
        "env", "printenv", "find", "grep", "egrep", "fgrep", "head",
        "ls", "mkdir", "rmdir", "mv", "ln", "readlink", "realpath",
        "rm", "sed", "sleep", "sort", "tail", "touch", "tr",
        "uname", "wc", "xargs", "echo", "printf", "pwd", "true",
        "false", "test", "date", "stat", "du", "df", "id",
        "whoami", "groups", "which", "seq", "yes", "tee", "uniq",
        "comm", "diff", "patch", "sha256sum", "sha512sum", "sha1sum", "md5sum",
        "cksum", "base64", "base32", "gzip", "gunzip", "zcat", "tar",
        "bzip2", "bunzip2", "bzcat", "unzip", "xz", "unxz", "xzcat",
        "ps", "top", "free", "uptime", "pidof", "kill", "killall",
        "nice", "nohup", "timeout", "wget", "httpd", "ping",
        "netstat", "nc", "crond", "crontab", "cal", "clear", "less",
        "more", "vi", "expr", "od", "hexdump", "strings", "split",
        "mkfifo", "mktemp"
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
