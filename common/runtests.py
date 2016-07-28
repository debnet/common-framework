# coding: utf-8
"""
A standalone test runner script, configuring the minimum settings required for tests to execute.
Re-use at your own risk: many Django applications will require different settings and/or templates to run their tests.
"""

import os
import sys


# Make sure the app is (at least temporarily) on the import path.
APP_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, APP_DIR)


# Minimum settings required for the app's tests.
SETTINGS_DICT = {
    'BASE_DIR': APP_DIR,
    'INSTALLED_APPS': (
        'django.contrib.admin',
        'django.contrib.auth',
        'django.contrib.contenttypes',
        'django.contrib.sessions',
        'django.contrib.sites',
        'rest_framework',
        'rest_framework.authtoken',
        'pytz',
        'common',
    ),
    'ROOT_URLCONF': 'common.tests.urls',
    'DATABASES': {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.path.join(APP_DIR, 'db.sqlite3'),
        },
    },
    'MIDDLEWARE_CLASSES': (
        'django.middleware.common.CommonMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
    ),
    'SITE_ID': 1,
    'TEMPLATE_DIRS': (
        os.path.join(APP_DIR, 'tests/templates'),
    ),
    'REST_FRAMEWORK': {
        'DEFAULT_PERMISSION_CLASSES': (
            'rest_framework.permissions.IsAuthenticated',
        ),
        'DEFAULT_AUTHENTICATION_CLASSES': (
            'rest_framework.authentication.TokenAuthentication',
            'rest_framework.authentication.SessionAuthentication',
        ),
        'DEFAULT_RENDERER_CLASSES': (
            'rest_framework.renderers.JSONRenderer',
        ),
        'DEFAULT_PARSER_CLASSES': (
            'rest_framework.parsers.JSONParser',
        ),
        'DEFAULT_PAGINATION_CLASS': 'common.api.pagination.CustomPageNumberPagination',
        'PAGE_SIZE': 10,
        'TEST_REQUEST_DEFAULT_FORMAT': 'json',
        'COERCE_DECIMAL_TO_STRING': True,
    },
    'NOTIFY_CHANGES': False,
    'LANGUAGE_CODE': 'fr',
    'TIME_ZONE': 'Europe/Paris',
    'USE_I18N': True,
    'USE_L10N': True,
    'USE_TZ': True,
}


def run_tests():
    # Making Django run this way is a two-step process. First,
    # call settings.configure() to give Django settings to work with:
    from django.conf import settings
    settings.configure(**SETTINGS_DICT)

    # Then, call django.setup() to initialize the application cache and other bits:
    import django
    if hasattr(django, 'setup'):
        django.setup()

    # Now we instantiate a test runner...
    from django.test.utils import get_runner
    TestRunner = get_runner(settings)

    # And then we run tests and return the results.
    test_runner = TestRunner(verbosity=1, interactive=True)
    failures = test_runner.run_tests(['common.tests'])
    sys.exit(bool(failures))


if __name__ == '__main__':  # pragma: no cover
    run_tests()             # pragma: no cover
