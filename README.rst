======
chrony
======

Formula to set up and configure chrony

Available states
================

.. contents::
    :local:

``chrony``
----------

Installs the chrony package.

``chrony.config``
-----------------
This state manages the file ``chrony.conf`` under ``/etc`` (template found in "chrony/files"). The configuration is populated by values in "chrony/map.jinja", which are based on the package's default values (for RedHat and Debian family distributions), and are overridden by values of the same name in pillar.

The package is installed before the configuration is managed, and configuration
changes restart the service. On Debian, ``rtconutc`` is omitted when the third
line of ``/etc/adjtime`` is ``LOCAL``. A missing or incomplete file leaves the
UTC default unchanged; no commands are run during rendering to detect RTC mode.

Offline tests
=============

With Python 3, Jinja2 and PyYAML installed, run::

    python3 -m unittest discover -s tests -v

These tests render the formula using stubbed Salt functions. They do not run
Salt states, read the host's RTC settings, or contact any hosts.
