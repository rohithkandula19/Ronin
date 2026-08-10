import unittest

from textkit import slugify as public_slugify
from textkit.index import build_index, lookup
from textkit.render import render_anchor, render_card
from textkit.slug import slugify


class SlugifyTests(unittest.TestCase):
    def test_stopwords_are_dropped(self):
        self.assertEqual(slugify("The Quick Brown Fox"), "quick-brown-fox")

    def test_accents_are_folded_to_ascii(self):
        self.assertEqual(slugify("Unicode Café"), "unicode-cafe")

    def test_punctuation_becomes_a_separator(self):
        self.assertEqual(slugify("hello, world!"), "hello-world")

    def test_a_title_made_only_of_stopwords_keeps_them(self):
        self.assertEqual(slugify("The And Of"), "the-and-of")

    def test_max_words_truncates(self):
        self.assertEqual(slugify("one two three four five", max_words=2), "one-two")

    def test_the_public_name_is_the_same_function(self):
        self.assertIs(public_slugify, slugify)


class UsageTests(unittest.TestCase):
    def test_render_card_slugs_the_title(self):
        card = render_card("The  Quick   Brown Fox", "a  b   c", width=5)
        self.assertEqual(card["slug"], "quick-brown-fox")
        self.assertEqual(card["title"], "The Quick Brown Fox")
        self.assertEqual(card["teaser"], "a b c")

    def test_render_anchor_caps_the_word_count(self):
        self.assertEqual(render_anchor("one two three four five"), "#one-two-three-four")

    def test_the_index_is_keyed_by_slug(self):
        index = build_index(["The Quick Brown Fox", "Unicode Café"])
        self.assertEqual(sorted(index), ["quick-brown-fox", "unicode-cafe"])
        self.assertEqual(lookup(index, "unicode  café"), "Unicode Café")


if __name__ == "__main__":
    unittest.main()
