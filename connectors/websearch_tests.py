"""web_search takes results that carry the query's words (websearch.py); the junk is what Bing's feed really returned
on the phone on 2026-10-31."""
import unittest
from unittest import mock

from . import tools, websearch

GOLD = "سعر الذهب اليوم في الأردن"
JUNK = [{"title": "Sound Could怎么注册用户？怎么上传自己的歌曲？ - 知乎", "url": "https://www.zhihu.com/question/1",
         "snippet": "SoundCloud"},
        {"title": "SoundCloud - 知乎", "url": "https://www.zhihu.com/topic/2", "snippet": ""},
        {"title": "Lidl stores in Great Britain", "url": "https://www.lidl.co.uk/s/en-GB/store-finder/",
         "snippet": "Find your Lidl store quickly"}]
GOOD = [{"title": "سعر الذهب في الاردن اليوم - تحديث كل 15 دقيقة", "url": "https://jo.gold-price-today.com/",
         "snippet": "سعر جرام الذهب عيار 21"},
        {"title": "رؤيا الإخباري | أسعار الذهب", "url": "https://royanews.tv/gold",
         "snippet": "أسعار الذهب في الأردن اليوم"},
        {"title": "سعر الذهب اليوم في الأردن مباشر | iGold24", "url": "https://igold24.com/jo", "snippet": ""}]
UK_WEATHER = [{"title": "BBC Weather - Official Site", "url": "https://www.bbc.com/weather", "snippet": "forecasts"},
              {"title": "Derby (Derby) weather - Met Office", "url": "https://weather.metoffice.gov.uk/forecast/gcqvn6pq4",
               "snippet": "Derby 7 day weather forecast"}]


class KeywordTests(unittest.TestCase):
    def test_arabic_words_are_folded_and_the_article_dropped(self):
        self.assertEqual(websearch.keywords(GOLD), ["سعر", "ذهب", "اردن"])

    def test_words_that_say_nothing_are_left_out(self):
        self.assertEqual(websearch.keywords("weather Amman today"), ["weather", "amman"])
        self.assertEqual(websearch.keywords("شو أخبار مباراة الريال اليوم؟"), ["اخبار", "مباراه", "ريال"])


class RelevanceTests(unittest.TestCase):
    def test_measured_junk_is_refused(self):
        self.assertEqual(websearch.relevant(JUNK, websearch.keywords(GOLD)), [])

    def test_results_about_the_question_are_kept(self):
        self.assertEqual(websearch.relevant(GOOD, websearch.keywords(GOLD)), GOOD)

    def test_sharing_one_word_of_two_is_not_enough(self):
        self.assertEqual(websearch.relevant(UK_WEATHER, websearch.keywords("weather Amman today")), [])

    def test_matching_results_come_first(self):
        rows = [JUNK[2]] + GOOD
        self.assertEqual(websearch.relevant(rows, websearch.keywords(GOLD))[:3], GOOD)


class SearchTests(unittest.TestCase):
    def engines(self, *answers):
        asked = []

        def engine(rows):
            def run(query):
                asked.append(query)
                if isinstance(rows, Exception):
                    raise rows
                return rows
            return run
        return mock.patch.object(websearch, "ENGINES", tuple(engine(r) for r in answers)), asked

    def test_next_engine_when_the_first_answers_with_junk(self):
        patch, asked = self.engines(JUNK, GOOD)
        with patch:
            self.assertEqual(websearch.search(GOLD), GOOD)
        self.assertEqual(len(asked), 2)

    def test_a_fitting_first_answer_asks_no_other_engine(self):
        patch, asked = self.engines(GOOD, JUNK)
        with patch:
            self.assertEqual(websearch.search(GOLD), GOOD)
        self.assertEqual(len(asked), 1)

    def test_failing_engine_is_skipped(self):
        patch, _ = self.engines(OSError("blocked"), GOOD)
        with patch:
            self.assertEqual(websearch.search(GOLD), GOOD)

    def test_when_nothing_fits_the_closest_list_is_returned(self):
        partial = [dict(r, title="الذهب") for r in JUNK]
        patch, _ = self.engines(JUNK, partial, [])
        with patch:
            self.assertEqual(websearch.search(GOLD), partial)

    def test_tool_uses_it(self):
        with mock.patch.object(websearch, "search", return_value=GOOD) as search:
            self.assertEqual(tools.web_search(GOLD), GOOD)
        search.assert_called_once_with(GOLD, 6)


if __name__ == "__main__":
    unittest.main()
