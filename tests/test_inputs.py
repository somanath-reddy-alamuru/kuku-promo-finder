import io, os, tempfile, unittest, zipfile

from promo_finder.inputs import InputError, MAX_FILE_BYTES, MAX_TOTAL_BYTES, MAX_FILES, parse_all, read_zip
from tests.helpers import script


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


EP1 = script([("CAMERA", 0, 3, "x"), ("ANNA", 0, 2, '"Hi"')], 1)
EP2 = script([("CAMERA", 0, 3, "x"), ("ANNA", 0, 2, '"Hi"')], 2)


class InputTests(unittest.TestCase):
    def test_nested_zip_and_junk(self):
        data = make_zip([("show/episode-001.txt", EP1), ("show/episode-002.txt", EP2),
                         ("__MACOSX/show/._episode-001.txt", "junk"), ("show/notes.pdf", "x"),
                         ("show/.hidden.txt", "x")])
        episodes, report = parse_all([("pack.zip", data)])
        self.assertEqual(sorted(episodes), [1, 2])
        self.assertTrue(any("notes.pdf" in s for s in report["skipped"]))

    def test_path_traversal_names_are_harmless(self):
        data = make_zip([("../../etc/episode-001.txt", EP1)])
        files, _ = read_zip(data)
        self.assertEqual(files[0][0], "episode-001.txt")      # only the base name is kept; nothing is written

    def test_malformed_zip(self):
        with self.assertRaises(InputError):
            parse_all([("pack.zip", b"this is not a zip")])

    def test_oversized_file_is_skipped(self):
        data = make_zip([("episode-001.txt", EP1), ("episode-002.txt", "x" * (MAX_FILE_BYTES + 1))])
        episodes, report = parse_all([("pack.zip", data)])
        self.assertEqual(list(episodes), [1])
        self.assertTrue(any("larger than" in s for s in report["skipped"]))

    def test_duplicates_and_unusable_files_are_reported(self):
        episodes, report = parse_all([("episode-001.txt", EP1), ("copy-of-ep1.txt", EP1),
                                      ("readme.txt", "hello")])
        self.assertEqual(list(episodes), [1])
        self.assertEqual(report["duplicates"][0][1], 1)
        self.assertEqual(len(report["duplicates"]), 1)
        self.assertEqual(report["unusable"][0][0], "readme.txt")

    def test_nothing_usable(self):
        with self.assertRaises(InputError):
            parse_all([("readme.txt", "hello")])
        with self.assertRaises(InputError):
            parse_all([("photo.png", b"\x89PNG")])

    def test_total_upload_size_is_limited_across_separate_files(self):
        chunk = b"x" * MAX_FILE_BYTES
        uploads = [(f"episode-{i:03}.txt", chunk) for i in range(1, MAX_TOTAL_BYTES // MAX_FILE_BYTES + 2)]
        with self.assertRaises(InputError):
            parse_all(uploads)

    def test_file_count_is_limited_across_separate_uploads(self):
        uploads = [(f"episode-{i:03}.txt", EP1) for i in range(MAX_FILES + 1)]
        with self.assertRaises(InputError):
            parse_all(uploads)

    def test_folder_and_single_file(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "episode-001.txt"), "w").write(EP1)
            self.assertEqual(list(parse_all(d)[0]), [1])
            self.assertEqual(list(parse_all(os.path.join(d, "episode-001.txt"))[0]), [1])
