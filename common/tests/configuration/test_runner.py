from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from common.runtests import run_tests


class TestRunnerTests(SimpleTestCase):
    def invoke(self, labels=None, failures=0):
        runner = Mock()
        runner.run_tests.return_value = failures
        factory = Mock(return_value=runner)
        with (
            patch("django.conf.LazySettings.configure"),
            patch("django.setup"),
            patch("django.test.utils.get_runner", return_value=factory),
            self.assertRaises(SystemExit) as exit_info,
        ):
            run_tests(labels)
        factory.assert_called_once_with(verbosity=1, interactive=False)
        return runner, exit_info.exception.code

    def test_default_suite(self):
        runner, code = self.invoke()
        runner.run_tests.assert_called_once_with(["common.tests"])
        self.assertEqual(code, 0)

    def test_domain_selection_and_failure_status(self):
        runner, code = self.invoke(["common.tests.forms"], failures=1)
        runner.run_tests.assert_called_once_with(["common.tests.forms"])
        self.assertEqual(code, 1)
