# coding: utf-8
import os
import re

from setuptools import setup


def get_version(package):
    """
    Return package version as listed in `__version__` in `init.py`.
    """
    init_py = open(os.path.join(package, "__init__.py")).read()
    return re.search("__version__ = ['\"]([^'\"]+)['\"]", init_py).group(1)


def get_packages(package):
    """
    Return root package and all sub-packages.
    """
    return [
        dirpath
        for dirpath, dirnames, filenames in os.walk(package)
        if os.path.exists(os.path.join(dirpath, "__init__.py"))
    ]


def get_package_data(package):
    """
    Return all files under the root package, that are not in a package themselves.
    """
    walk = [
        (dirpath.replace(package + os.sep, "", 1), filenames)
        for dirpath, dirnames, filenames in os.walk(package)
        if not os.path.exists(os.path.join(dirpath, "__init__.py"))
    ]
    filepaths = []
    for base, filenames in walk:
        filepaths.extend([os.path.join(base, filename) for filename in filenames])
    return {package: filepaths}


setup(
    name="common-framework",
    version=get_version("common"),
    description="All-in-one framework for Django and Django REST Framework",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Marc Debureaux",
    author_email="marc@debureaux.fr",
    url="https://github.com/debnet/common-framework",
    package_dir={"common": "common"},
    packages=get_packages("common"),
    package_data=get_package_data("common"),
    test_suite="common.runtests.run_tests",
    py_modules=["common"],
    zip_safe=False,
    python_requires=">=3.13",
    license="MIT",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Web Environment",
        "Framework :: Django",
        "Framework :: Django :: 6.0",
        "Framework :: Django :: 6.1",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Programming Language :: Python :: 3.15",
        "Topic :: Utilities",
    ],
    install_requires=[
        "Django>=6.0",
        "djangorestframework>=3.18.3",
        "python-dateutil>=2.8.2,<3",
        "pytz>=2025.2",
    ],
    extras_require={
        "celery": ["celery>=5.5.3"],
        "excel": ["openpyxl>=3.1"],
        "ldap": ["ldap3>=2.9"],
        "webhooks": ["requests>=2.32.5"],
        "websocket": ["websocket-client>=1.8", "autobahn>=24.4"],
        "markdown": ["markdown2>=2.5"],
        "csv": ["djangorestframework-csv>=3"],
        "postgresql": ["psycopg[binary]>=3.2"],
    },
)
