import datetime

from django.test import TestCase

from common.settings import settings
from common.utils import parsedate


class UtilsTestCase(TestCase):
    def test_parsedate_none(self):
        self.assertIsNone(parsedate("chaine"))

    def test_parsedate_initial(self):
        tab_format_date = ["2015-01-01", "20150101", "01-01-2015", "01/01/2015", "2015/01/01"]
        for format_date in tab_format_date:
            self.assertEqual(parsedate(format_date).year, 2015)
            self.assertEqual(parsedate(format_date).month, 1)
            self.assertEqual(parsedate(format_date).day, 1)
            self.assertEqual(parsedate(format_date).hour, 0)
            self.assertEqual(parsedate(format_date).minute, 0)
            self.assertEqual(parsedate(format_date).second, 0)
            self.assertEqual(parsedate(format_date).microsecond, 0)

    def test_parsedate_start_day(self):
        tab_format_date = ["2015-01-01", "20150101", "01-01-2015", "01/01/2015", "2015/01/01"]
        for format_date in tab_format_date:
            self.assertEqual(parsedate(format_date).year, 2015)
            self.assertEqual(parsedate(format_date).month, 1)
            self.assertEqual(parsedate(format_date).day, 1)
            self.assertEqual(parsedate(format_date, start_day=True).hour, 0)
            self.assertEqual(parsedate(format_date, start_day=True).minute, 0)
            self.assertEqual(parsedate(format_date, start_day=True).second, 0)
            self.assertEqual(parsedate(format_date, start_day=True).microsecond, 0)

    def test_parsedate_end_day(self):
        tab_format_date = ["2015-01-01", "20150101", "01-01-2015", "01/01/2015", "2015/01/01"]
        for format_date in tab_format_date:
            self.assertEqual(parsedate(format_date).year, 2015)
            self.assertEqual(parsedate(format_date).month, 1)
            self.assertEqual(parsedate(format_date).day, 1)
            self.assertEqual(parsedate(format_date, end_day=True).hour, 23)
            self.assertEqual(parsedate(format_date, end_day=True).minute, 59)
            self.assertEqual(parsedate(format_date, end_day=True).second, 59)
            self.assertEqual(parsedate(format_date, end_day=True).microsecond, 999999)

    def test_parsedate_start_day_end_day(self):
        tab_format_date = ["2015-01-01", "20150101", "01-01-2015", "01/01/2015", "2015/01/01"]
        for format_date in tab_format_date:
            self.assertEqual(parsedate(format_date).year, 2015)
            self.assertEqual(parsedate(format_date).month, 1)
            self.assertEqual(parsedate(format_date).day, 1)
            self.assertEqual(parsedate(format_date, start_day=True, end_day=True).hour, 0)
            self.assertEqual(parsedate(format_date, start_day=True, end_day=True).minute, 0)
            self.assertEqual(parsedate(format_date, start_day=True, end_day=True).second, 0)
            self.assertEqual(parsedate(format_date, start_day=True, end_day=True).microsecond, 0)

    def test_parsedate_timezone(self):
        tab_format_date = ["2015-01-01", "20150101", "01-01-2015", "01/01/2015", "2015/01/01"]
        for format_date in tab_format_date:
            self.assertEqual(parsedate(format_date).tzinfo.zone, settings.TIME_ZONE)

    def test_parsedate_format_date(self):
        tab_format_date = ["2015-01-01", "20150101", "01-01-2015", "01/01/2015", "2015/01/01"]
        for format_date in tab_format_date:
            self.assertIsInstance(parsedate(format_date, date_only=True), datetime.date)

    def test_parsedate_format_datetime(self):
        tab_format_date = ["2015-01-01", "20150101", "01-01-2015", "01/01/2015", "2015/01/01"]
        for format_date in tab_format_date:
            self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_utc(self):
        tab_format_date = ["2015-01-01", "20150101", "01-01-2015", "01/01/2015", "2015/01/01"]
        for format_date in tab_format_date:
            self.assertEqual(parsedate(format_date, utc=True).tzinfo.zone, "UTC")

    def test_parsedatetime_datetime(self):
        tab_format_datetime = [
            "2015-01-01 18:25:52",
            "20150101 18:25:52",
            "01-01-2015 18:25:52",
            "01/01/2015 18:25:52",
            "2015/01/01 18:25:52",
        ]
        for format_datetime in tab_format_datetime:
            self.assertIsInstance(parsedate(format_datetime), datetime.datetime)

    def test_parsedatetime(self):
        tab_format_date = ["2015-01-01 150205", "20150101 15:02:05"]
        for format_date in tab_format_date:
            self.assertEqual(parsedate(format_date).year, 2015)
            self.assertEqual(parsedate(format_date).month, 1)
            self.assertEqual(parsedate(format_date).day, 1)
            self.assertEqual(parsedate(format_date, start_day=True, end_day=True).hour, 15)
            self.assertEqual(parsedate(format_date, start_day=True, end_day=True).minute, 2)
            self.assertEqual(parsedate(format_date, start_day=True, end_day=True).second, 5)
            self.assertEqual(parsedate(format_date, start_day=True, end_day=True).microsecond, 0)

    def test_parsedate_bisextil_ko(self):
        format_date = "2015-02-29"
        self.assertIsNone(parsedate(format_date))

    def test_parsedate_bisextil_ok(self):
        format_date = "2016-02-29"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_format_ko(self):
        format_date = "01012016"
        self.assertIsNone(parsedate(format_date))

    def test_parsedate_ecrit_dddddmmyyyy(self):
        format_date = "Thu, 25 Dec 2003"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_ecrit_dddddmmyyyyhhmmss(self):
        format_date = "Thu, 25 Dec 2003 10:49:41 -0300"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_ecrit_mmmddyyyy(self):
        format_date = "Dec 25 2003"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_ecrit_mmmyyyy(self):
        format_date = "Dec 2003"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_ecrit_mmm(self):
        format_date = "Dec"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_ecrit_yyyy(self):
        format_date = "2003"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_ecrit_yyyymmddthhmmssxtz(self):
        format_date = "2003-09-25T10:49:41.5-03:00"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_isoformat_yyyymmddthhmmssxtz(self):
        format_date = "20030925T104941.5-0300"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_isoformat_yyyymmddthhmmsstz(self):
        format_date = "20030925T104941-0300"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_isoformat_yyyymmddthhmmss(self):
        format_date = "20030925T104941"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_isoformat_yyyymmddthhmm(self):
        format_date = "20030925T1049"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_isoformat_yyyymmddthh(self):
        format_date = "20030925T10"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_parsedate_isoformat_yyyymmdd(self):
        format_date = "20030925"
        self.assertIsInstance(parsedate(format_date), datetime.datetime)

    def test_obj_datetime_ok(self):
        from datetime import datetime

        self.assertIsInstance(parsedate(datetime(2000, 1, 1)), datetime)
        self.assertEqual(parsedate(datetime(2000, 1, 1)).year, 2000)
        self.assertEqual(parsedate(datetime(2000, 1, 1)).month, 1)
        self.assertEqual(parsedate(datetime(2000, 1, 1)).day, 1)

    def test_obj_datetime_ko(self):
        from datetime import datetime

        d = None
        try:
            d = datetime(2000, 13, 1)
        except Exception:
            pass
        finally:
            self.assertIsNone(d)
