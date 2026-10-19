"""One document per publication, and one indexed text per document."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nawantara_nextcloud.records import classify, group, normalize, stem


class Naming(unittest.TestCase):
    def test_producer_affixes_reduce_to_the_source_name(self) -> None:
        wanted = normalize('POJK 22 Tahun 2023 Pelindungan Konsumen')
        for name in (
            'POJK 22 Tahun 2023 Pelindungan Konsumen.pdf',
            'MinerU_markdown_POJK_22_Tahun_2023_Pelindungan_Konsumen_2096144854990213120.md',
            'MinerU_POJK 22 Tahun 2023 Pelindungan Konsumen__20260905075409.json',
        ):
            self.assertEqual(stem(name), wanted, name)

    def test_paddleocr_suffix_and_repeated_extension_are_removed(self) -> None:
        wanted = normalize('Panduan Resiliensi Digital (Digital Resilience) 2024')
        self.assertEqual(stem('Panduan Resiliensi Digital (Digital Resilience) 2024.pdf'), wanted)
        self.assertEqual(
            stem('Panduan Resiliensi Digital (Digital Resilience) 2024.pdf_by_PaddleOCR-VL-1.6.md'), wanted
        )
        self.assertEqual(
            stem('PERMA-04-2025-TATACARA PENGADILAN OJK.pdf.json'),
            normalize('PERMA-04-2025-TATACARA PENGADILAN OJK'),
        )

    def test_json_is_structure_and_never_text(self) -> None:
        self.assertEqual(classify('/R/doc.json'), 'structure')
        self.assertEqual(classify('/R/doc.md'), 'text')
        self.assertEqual(classify('/R/doc.pdf'), 'original')
        self.assertEqual(classify('/R/deck.pptx'), 'file')


class Grouping(unittest.TestCase):
    def test_publication_with_three_representations_is_one_document(self) -> None:
        got = group(
            [
                ('1', '/R/POJK 22 Tahun 2023.pdf'),
                ('2', '/R/MinerU_markdown_POJK_22_Tahun_2023_2096144854990213120.md'),
                ('3', '/R/MinerU_POJK 22 Tahun 2023__20260905075409.json'),
            ]
        )
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0].record_id, got[0].text_file_id, got[0].structure_file_id), ('1', '2', '3'))
        self.assertEqual(len(got[0].representations), 3)

    def test_two_publications_in_one_folder_stay_separate(self) -> None:
        got = group(
            [
                ('1', '/R/PADG-322025-Rev.pdf'),
                ('2', '/R/Petunjuk-Teknis-Pembayaran.pdf'),
                ('3', '/R/MinerU_markdown_PADG-322025-Rev_2099836668536442880.md'),
                ('4', '/R/MinerU_markdown_Petunjuk-Teknis-Pembayaran_2099837764684242944.md'),
            ]
        )
        self.assertEqual(len(got), 2)
        self.assertEqual({item.text_file_id for item in got}, {'3', '4'})

    def test_markdown_without_a_pdf_is_still_indexable(self) -> None:
        got = group([('1', '/R/notes.md')])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].text_file_id, '1')

    def test_structure_without_an_original_is_not_indexed(self) -> None:
        self.assertEqual(group([('1', '/R/orphan.json')]), [])

    def test_derived_deliverables_are_their_own_documents(self) -> None:
        got = group(
            [
                ('1', '/P/POJK 22 Tahun 2023.pdf'),
                ('2', '/P/output/Kertas_Kerja_Bank.xlsx'),
                ('3', '/P/output/Panduan_Alur.docx'),
            ]
        )
        self.assertEqual({item.record_id for item in got}, {'1', '2', '3'})
        self.assertEqual(len(got[0].representations), 1)


if __name__ == '__main__':
    unittest.main()
