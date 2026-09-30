from datetime import datetime, timezone
import unittest

from jifeng_erp.archive_versions import same_version


class NativeArchiveVersions(unittest.TestCase):
    def test_native_save_response_space_and_database_iso_match(self):
        current = datetime(2026, 10, 1, 12, 30, 5, 123456)
        self.assertTrue(same_version(current, "2026-10-01 12:30:05.123456"))
        self.assertTrue(same_version("2026-10-01T12:30:05.123456", "2026-10-01 12:30:05.123456"))
        self.assertTrue(same_version("2026-10-01 12:30:05.123456", "2026-10-01T12:30:05.123456"))

    def test_zero_or_short_fraction_does_not_change_the_timestamp(self):
        self.assertTrue(same_version(datetime(2026, 10, 1), "2026-10-01 00:00:00.000000"))
        self.assertTrue(same_version(datetime(2026, 10, 1, microsecond=120000), "2026-10-01T00:00:00.12"))

    def test_one_microsecond_or_second_is_a_different_version(self):
        current = datetime(2026, 10, 1, microsecond=123456)
        self.assertFalse(same_version(current, "2026-10-01T00:00:00.123455"))
        self.assertFalse(same_version(current, "2026-10-01T00:00:01.123456"))

    def test_invalid_missing_or_timezone_values_cannot_bypass_the_version(self):
        for value in (None, True, 0, {}, "", "stale", "2026-10-01", "20261001T000000",
                      "2026-10-01T00:00:00.1234567", "2026-10-01T00:00:00+00:00", "2026-13-01T00:00:00"):
            with self.subTest(value=value):
                self.assertFalse(same_version(datetime(2026, 10, 1), value))
        self.assertFalse(same_version(datetime(2026, 10, 1, tzinfo=timezone.utc), "2026-10-01T00:00:00"))


if __name__ == "__main__":
    unittest.main()
