"""How the task memory turns text into search terms. TaskMemoryText.java does exactly the same on the phone: change
one, change both (task_memory_tests.py and TaskMemoryTextTest.java check the same examples)."""
import unicodedata

EN_STOP = set("""a an the to of in on for and or with my me i it its is are at by from this that these those be as
please then now your you all some any can could will would into onto about up out over via using use app application
just also very so do does did done get got go going want need should must there here what which who whom whose
whether if when while than too own same such only both each few more most other our ours their them they he she his
her we us was were been being have has had having not no nor""".split())
AR_STOP = set(TEXT.strip() for TEXT in """في من على الى إلى عن مع و يا لو بس هلق هلا شي اي أي هذا هاد هيدا هذه هاي هالـ
يلي اللي الي ان أن إن انه كان بدي اريد أريد ممكن لي عندي انا أنا انت أنت هو هي نحن هم كل بعض لما لـ ما ماذا شو
ليش وين كيف متى هل قد لقد ثم بعدين وبعدين بعد قبل عند حتى او أو كمان كذلك منها منه فيها فيه عليه عليها لها له
بها به لك اياه إياه هيك هون هناك تبع تبعي تبعك بال يعني رجاء رجاءا لوسمحت سمحت فضلك من فضلك""".split())

AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ف", "ب", "ل")
AR_SUFFIXES = ("ها", "هم", "كم", "نا", "ني", "لي", "ات", "ين", "ون", "ه", "ي")


def fold(text):
    """TextFold.fold: lower case; أ إ آ ٱ -> ا, ة -> ه, ى -> ي; no diacritics or tatweel; one space between words."""
    s = str(text or "").lower()
    out = []
    space = False
    for c in s:
        o = ord(c)
        if 0x064B <= o <= 0x0652 or c in "ٰـ":
            continue
        if c in "أإآٱ":
            c = "ا"
        elif c == "ة":
            c = "ه"
        elif c == "ى":
            c = "ي"
        if c.isspace():
            if out and not space:
                out.append(" ")
            space = True
            continue
        space = False
        out.append(c)
    if out and out[-1] == " ":
        out.pop()
    return "".join(out)


def _is_word_char(c):
    if c.isdigit():
        return True
    cat = unicodedata.category(c)
    return cat.startswith("L") or cat == "Mn" and False


def words(text):
    """The words of folded text: letters and digits, split on everything else."""
    out, cur = [], []
    for c in fold(text):
        if c.isalpha() or c.isdigit():
            cur.append(c)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def stem_en(w):
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and (w.endswith("ches") or w.endswith("shes") or w.endswith("sses") or w.endswith("xes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss") and not w.endswith("us"):
        return w[:-1]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    return w


def variants_ar(w):
    """An Arabic word and the forms it may have without one leading particle (و ف ب ل ال...) and/or one ending
    (ها ات ين ه ي...): "وانشر" -> {وانشر, انشر}; "فيسبوك" keeps "فيسبوك". Both the index and a request use every
    form, so "وانشر" finds "انشر" while a name that only looks like it has a prefix still finds itself."""
    out = [w]
    heads = [w]
    for p in AR_PREFIXES:
        if w.startswith(p) and len(w) - len(p) >= 3:
            heads.append(w[len(p):])
            break
    for h in heads:
        if h not in out:
            out.append(h)
        for s in AR_SUFFIXES:
            if h.endswith(s) and len(h) - len(s) >= 3:
                if h[:-len(s)] not in out:
                    out.append(h[:-len(s)])
                break
    return out


def is_ascii(w):
    return all(ord(c) < 128 for c in w)


def word_terms(w):
    """The search terms of one word: [] for a stop word, the stem of an English word, every form of an Arabic one."""
    if is_ascii(w):
        if w in EN_STOP or len(w) < 2:
            return []
        return [stem_en(w)]
    if w in AR_STOP:
        return []
    return [v for v in variants_ar(w) if len(v) >= 2]


def terms(text):
    """Every term of the text, for the index (in order, repeated forms kept once per word)."""
    out = []
    for w in words(text):
        out.extend(word_terms(w))
    return out


def query(text):
    """The request as a list of words, each the list of its terms."""
    out = []
    for w in words(text):
        t = word_terms(w)
        if t:
            out.append(t)
    return out
