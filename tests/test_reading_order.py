"""Reading order: column detection and column-by-column ordering.

Pages are built in memory with PyMuPDF so the word boxes are real.
"""
from tests import sandbox  # noqa: F401  (must be first: isolates user folders)

import random
import unittest

import fitz

import app

W, H = 612, 792


def make_page(draw):
    """Build a one-page PDF via draw(page), round-trip it, return the page."""
    doc = fitz.open()
    draw(doc.new_page(width=W, height=H))
    return fitz.open(stream=doc.tobytes(), filetype='pdf')[0]


def analyse(page):
    raw = page.get_text('words')
    split = app._detect_column_split(raw, page.rect.width)
    words = [w[4] for w in app.sort_words_reading_order(raw, page.rect.width)]
    return split, words, ' '.join(words)


def column1_before_column2(text):
    return text.rfind('LEFTcol') < text.find('RIGHTcol')


class TwoColumnPages(unittest.TestCase):
    def test_plain_two_columns(self):
        def draw(p):
            for y in range(80, 720, 14):
                p.insert_text((72, y), 'LEFTcol aaa bbb ccc')
                p.insert_text((330, y), 'RIGHTcol xxx yyy zzz')
        split, _, text = analyse(make_page(draw))
        self.assertIsNotNone(split)
        self.assertTrue(column1_before_column2(text))

    def test_full_width_header_then_two_columns(self):
        # Journal first page: title/authors/abstract span the page, then columns.
        def draw(p):
            p.insert_text((72, 90), 'Identification of RNA binding proteins direct effects')
            p.insert_text((72, 118), 'KAILEY WORNER QIUYING LIU and WENQIAN HU')
            for y in range(150, 280, 14):
                p.insert_text((72, y), 'abstract text spanning the full page width with continuous justified prose')
            for y in range(310, 720, 14):
                p.insert_text((72, y), 'LEFTcol alpha beta gamma')
                p.insert_text((330, y), 'RIGHTcol delta epsilon zeta')
        split, words, text = analyse(make_page(draw))
        self.assertIsNotNone(split)
        self.assertEqual(words[0], 'Identification')            # header read first
        self.assertTrue(column1_before_column2(text))

    def test_spanning_title_stays_intact(self):
        title = 'A FULL WIDTH TITLE SPANNING ACROSS BOTH COLUMNS HERE'

        def draw(p):
            p.insert_text((100, 60), title)
            for y in range(100, 720, 14):
                p.insert_text((72, y), 'LEFTcol aaa bbb ccc')
                p.insert_text((330, y), 'RIGHTcol xxx yyy zzz')
        _, _, text = analyse(make_page(draw))
        self.assertIn(title, text)

    def test_columns_that_are_not_baseline_aligned(self):
        def draw(p):
            for y in range(80, 720, 14):
                p.insert_text((72, y), 'LEFTcol word one two three')
            for y in range(87, 720, 14):                         # offset baselines
                p.insert_text((330, y), 'RIGHTcol word four five six')
        split, _, text = analyse(make_page(draw))
        self.assertIsNotNone(split)
        self.assertTrue(column1_before_column2(text))


class SingleColumnPages(unittest.TestCase):
    """None of these may be mistaken for two columns."""

    def assertSingleColumn(self, draw):
        split, _, _ = analyse(make_page(draw))
        self.assertIsNone(split)

    def test_continuous_prose(self):
        self.assertSingleColumn(lambda p: [
            p.insert_text((72, y), 'single column continuous prose running the full width of the text area')
            for y in range(80, 720, 14)])

    def test_narrow_centred_block(self):
        self.assertSingleColumn(lambda p: [
            p.insert_text((210, y), 'centred narrow column text block here')
            for y in range(80, 720, 14)])

    def test_ragged_right_edge(self):
        rng = random.Random(3)
        self.assertSingleColumn(lambda p: [
            p.insert_text((72, y), ' '.join(['word'] * rng.choice([6, 7, 8, 9])))
            for y in range(80, 720, 14)])

    def test_indented_paragraphs(self):
        self.assertSingleColumn(lambda p: [
            p.insert_text((100 if i % 6 == 0 else 72, y),
                          'continuous body text spanning the whole width of the page here today')
            for i, y in enumerate(range(80, 720, 14))])

    def test_sparse_title_page(self):
        def draw(p):
            p.insert_text((72, 300), 'A Book Title')
            p.insert_text((72, 330), 'by Some Author')
        self.assertSingleColumn(draw)


if __name__ == '__main__':
    unittest.main()
