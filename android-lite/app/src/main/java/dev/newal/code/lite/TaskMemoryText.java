package dev.newal.code.lite;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * How the task memory turns text into search terms, exactly as tools/task_memory/textnorm.py does when it builds the
 * database: the words of the folded text (TextFold), English words stemmed (posts -> post, searching -> search),
 * Arabic words with their forms without one leading particle and/or one ending ("وانشر" also "انشر"), stop words out.
 * TaskMemoryTextTest checks the same examples as the Python tests.
 */
final class TaskMemoryText {
    private TaskMemoryText() {
    }

    static final Set<String> EN_STOP = new HashSet<>(Arrays.asList((
            "a an the to of in on for and or with my me i it its is are at by from this that these those be as "
            + "please then now your you all some any can could will would into onto about up out over via using use app "
            + "application just also very so do does did done get got go going want need should must there here what "
            + "which who whom whose whether if when while than too own same such only both each few more most other our "
            + "ours their them they he she his her we us was were been being have has had having not no nor").split(" ")));

    static final Set<String> AR_STOP = new HashSet<>(Arrays.asList((
            "في من على الى إلى عن مع و يا لو بس هلق هلا شي اي أي هذا هاد هيدا هذه هاي هالـ يلي اللي الي ان أن إن "
            + "انه كان بدي اريد أريد ممكن لي عندي انا أنا انت أنت هو هي نحن هم كل بعض لما لـ ما ماذا شو ليش وين كيف "
            + "متى هل قد لقد ثم بعدين وبعدين بعد قبل عند حتى او أو كمان كذلك منها منه فيها فيه عليه عليها لها له بها "
            + "به لك اياه إياه هيك هون هناك تبع تبعي تبعك بال يعني رجاء رجاءا لوسمحت سمحت فضلك من فضلك").split(" ")));

    private static final String[] AR_PREFIXES = {"وال", "بال", "كال", "فال", "لل", "ال", "و", "ف", "ب", "ل"};
    private static final String[] AR_SUFFIXES = {"ها", "هم", "كم", "نا", "ني", "لي", "ات", "ين", "ون", "ه", "ي"};

    /** The words of the folded text: runs of letters and digits. */
    static List<String> words(CharSequence text) {
        String f = TextFold.fold(text);
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        for (int i = 0; i < f.length(); ) {
            int cp = f.codePointAt(i);
            i += Character.charCount(cp);
            if (Character.isLetter(cp) || Character.isDigit(cp)) {
                cur.appendCodePoint(cp);
            } else if (cur.length() > 0) {
                out.add(cur.toString());
                cur.setLength(0);
            }
        }
        if (cur.length() > 0) {
            out.add(cur.toString());
        }
        return out;
    }

    static String stemEn(String w) {
        int n = w.length();
        if (n > 5 && w.endsWith("ing")) {
            return w.substring(0, n - 3);
        }
        if (n > 4 && w.endsWith("ies")) {
            return w.substring(0, n - 3) + "y";
        }
        if (n > 4 && (w.endsWith("ches") || w.endsWith("shes") || w.endsWith("sses") || w.endsWith("xes"))) {
            return w.substring(0, n - 2);
        }
        if (n > 3 && w.endsWith("s") && !w.endsWith("ss") && !w.endsWith("us")) {
            return w.substring(0, n - 1);
        }
        if (n > 4 && w.endsWith("ed")) {
            return w.substring(0, n - 2);
        }
        return w;
    }

    /** An Arabic word and its forms without one leading particle and/or one ending (textnorm.variants_ar). */
    static List<String> variantsAr(String w) {
        List<String> out = new ArrayList<>();
        out.add(w);
        List<String> heads = new ArrayList<>();
        heads.add(w);
        for (String p : AR_PREFIXES) {
            if (w.startsWith(p) && w.length() - p.length() >= 3) {
                heads.add(w.substring(p.length()));
                break;
            }
        }
        for (String h : heads) {
            if (!out.contains(h)) {
                out.add(h);
            }
            for (String s : AR_SUFFIXES) {
                if (h.endsWith(s) && h.length() - s.length() >= 3) {
                    String cut = h.substring(0, h.length() - s.length());
                    if (!out.contains(cut)) {
                        out.add(cut);
                    }
                    break;
                }
            }
        }
        return out;
    }

    static boolean ascii(String w) {
        for (int i = 0; i < w.length(); i++) {
            if (w.charAt(i) >= 128) {
                return false;
            }
        }
        return true;
    }

    /** The terms of one word: none for a stop word, the stem of an English word, every form of an Arabic one. */
    static List<String> wordTerms(String w) {
        List<String> out = new ArrayList<>();
        if (ascii(w)) {
            if (EN_STOP.contains(w) || w.length() < 2) {
                return out;
            }
            out.add(stemEn(w));
            return out;
        }
        if (AR_STOP.contains(w)) {
            return out;
        }
        for (String v : variantsAr(w)) {
            if (v.length() >= 2) {
                out.add(v);
            }
        }
        return out;
    }

    /** Every term of the text, in order (for ranking a procedure). */
    static List<String> terms(CharSequence text) {
        List<String> out = new ArrayList<>();
        for (String w : words(text)) {
            out.addAll(wordTerms(w));
        }
        return out;
    }

    /** A request as its words, each the list of its terms (textnorm.query). */
    static List<List<String>> query(CharSequence text) {
        List<List<String>> out = new ArrayList<>();
        for (String w : words(text)) {
            List<String> t = wordTerms(w);
            if (!t.isEmpty()) {
                out.add(t);
            }
        }
        return out;
    }
}
