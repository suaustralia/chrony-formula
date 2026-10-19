"""Offline formula rendering tests; no Salt installation or host access needed."""

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]


class FormulaRenderer:
    def __init__(self, os_family="Debian", config=None, adjtime=None):
        self.os_family = os_family
        self.config = config if config is not None else {}
        self.file_exists = Mock(return_value=adjtime is not None)
        self.file_read = Mock(return_value=adjtime)
        self.environment = Environment(
            loader=FileSystemLoader(str(ROOT)),
            undefined=StrictUndefined,
            extensions=["jinja2.ext.do"],
        )
        self.environment.globals.update(
            grains={"os_family": os_family},
            salt={
                "grains.filter_by": self.filter_by,
                "pillar.get": self.pillar_get,
                "file.file_exists": self.file_exists,
                "file.read": self.file_read,
                # Deliberately no cmd functions: rendering must not run commands.
            },
        )

    def pillar_get(self, key):
        if key != "chrony:config":
            raise AssertionError("Unexpected pillar key: " + key)
        return deepcopy(self.config)

    def filter_by(self, lookup, grain, merge):
        if grain != "os_family":
            raise AssertionError("Unexpected grain: " + grain)
        # All map values are scalars or lists, which Salt replaces on merge.
        result = deepcopy(lookup[self.os_family])
        result.update(deepcopy(merge))
        return result

    def render(self, name):
        return self.environment.get_template(name).render()

    def state(self, name):
        return yaml.safe_load(self.render(name))

    def directives(self):
        return [
            line.strip()
            for line in self.render("chrony/files/chrony_config").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]


def state_arguments(state, state_id, function):
    arguments = {}
    for item in state[state_id][function]:
        arguments.update(item)
    return arguments


class RTCRenderTests(unittest.TestCase):
    def test_debian_utc_retains_rtconutc(self):
        renderer = FormulaRenderer(adjtime="0.0 0 0.0\n0\nUTC\n")
        self.assertIn("rtconutc", renderer.directives())
        renderer.file_exists.assert_called_once_with("/etc/adjtime")
        renderer.file_read.assert_called_once_with("/etc/adjtime")

    def test_debian_local_omits_rtconutc(self):
        renderer = FormulaRenderer(adjtime="0.0 0 0.0\n0\nLOCAL\n")
        directives = renderer.directives()
        self.assertNotIn("rtconutc", directives)
        self.assertIn("maxupdateskew 100.0", directives)
        renderer.file_read.assert_called_once_with("/etc/adjtime")

    def test_missing_adjtime_assumes_utc_without_reading(self):
        renderer = FormulaRenderer()
        self.assertIn("rtconutc", renderer.directives())
        renderer.file_exists.assert_called_once_with("/etc/adjtime")
        renderer.file_read.assert_not_called()

    def test_incomplete_or_unrecognized_adjtime_keeps_utc(self):
        for adjtime in ("", "0.0 0 0.0\n", "0.0 0 0.0\n0\n", "0\n0\nOTHER\n"):
            with self.subTest(adjtime=adjtime):
                renderer = FormulaRenderer(adjtime=adjtime)
                self.assertIn("rtconutc", renderer.directives())

    def test_local_with_whitespace(self):
        renderer = FormulaRenderer(adjtime="0.0 0 0.0\r\n0\r\n LOCAL \r\n")
        self.assertNotIn("rtconutc", renderer.directives())

    def test_only_third_line_determines_rtc_mode(self):
        renderer = FormulaRenderer(adjtime="LOCAL\n0\nUTC\nLOCAL\n")
        self.assertIn("rtconutc", renderer.directives())

    def test_custom_otherparams_without_rtconutc(self):
        for otherparams in ([], ["rtcsync", "makestep 1 3"]):
            with self.subTest(otherparams=otherparams):
                config = {"otherparams": otherparams}
                renderer = FormulaRenderer(config=config, adjtime="0\n0\nLOCAL\n")
                directives = renderer.directives()
                self.assertNotIn("rtconutc", directives)
                self.assertNotIn("maxupdateskew 100.0", directives)
                for param in otherparams:
                    self.assertIn(param, directives)
                self.assertEqual(config["otherparams"], otherparams)
                renderer.file_exists.assert_not_called()
                renderer.file_read.assert_not_called()

    def test_custom_otherparams_with_rtconutc(self):
        config = {"otherparams": ["rtconutc", "makestep 1 3"]}
        renderer = FormulaRenderer(config=config, adjtime="0\n0\nLOCAL\n")
        directives = renderer.directives()
        self.assertNotIn("rtconutc", directives)
        self.assertIn("makestep 1 3", directives)
        self.assertEqual(config["otherparams"], ["rtconutc", "makestep 1 3"])

    def test_redhat_never_probes_adjtime(self):
        for config in ({}, {"otherparams": ["rtconutc", "rtcsync"]}):
            with self.subTest(config=config):
                renderer = FormulaRenderer("RedHat", config, "0\n0\nLOCAL\n")
                directives = renderer.directives()
                self.assertIn("rtcsync", directives)
                if config:
                    self.assertIn("rtconutc", directives)
                renderer.file_exists.assert_not_called()
                renderer.file_read.assert_not_called()


class StateRenderTests(unittest.TestCase):
    def test_package_configuration_service_order_and_restart(self):
        for os_family, service, path in (
            ("Debian", "chrony", "/etc/chrony/chrony.conf"),
            ("RedHat", "chronyd", "/etc/chrony.conf"),
        ):
            with self.subTest(os_family=os_family):
                renderer = FormulaRenderer(os_family)
                initial = renderer.state("chrony/init.sls")
                configuration = renderer.state("chrony/config.sls")
                package = state_arguments(initial, "chrony", "pkg.installed")
                daemon = state_arguments(initial, "chrony", "service.running")
                managed = state_arguments(configuration, "chrony_config", "file.managed")
                self.assertEqual(configuration["include"], ["chrony"])
                self.assertEqual(package["name"], "chrony")
                self.assertEqual(daemon["name"], service)
                self.assertIs(daemon["enable"], True)
                self.assertEqual(daemon["require"], [{"pkg": "chrony"}])
                self.assertEqual(managed["require"], [{"pkg": "chrony"}])
                self.assertEqual(managed["watch_in"], [{"service": "chrony"}])
                self.assertEqual(managed["name"], path)
                self.assertEqual(managed["mode"], "0644")
                self.assertEqual(managed["user"], "root")
                self.assertEqual(managed["template"], "jinja")
                self.assertEqual(managed["source"], "salt://chrony/files/chrony_config")

    def test_pillar_overrides_keep_requisites_on_state_ids(self):
        renderer = FormulaRenderer(config={
            "package": "custom-chrony",
            "service": "custom-chronyd",
            "config": "/etc/chrony/custom.conf",
            "config_src": "salt://custom/chrony.conf",
        })
        initial = renderer.state("chrony/init.sls")
        configuration = renderer.state("chrony/config.sls")
        package = state_arguments(initial, "chrony", "pkg.installed")
        daemon = state_arguments(initial, "chrony", "service.running")
        managed = state_arguments(configuration, "chrony_config", "file.managed")
        self.assertEqual(package["name"], "custom-chrony")
        self.assertEqual(daemon["name"], "custom-chronyd")
        self.assertEqual(daemon["require"], [{"pkg": "chrony"}])
        self.assertEqual(managed["require"], [{"pkg": "chrony"}])
        self.assertEqual(managed["watch_in"], [{"service": "chrony"}])
        self.assertEqual(managed["name"], "/etc/chrony/custom.conf")
        self.assertEqual(managed["source"], "salt://custom/chrony.conf")

    def test_local_rtc_states_render_safely(self):
        for config in ({}, {"otherparams": []}, {"otherparams": ["rtcsync"]}):
            with self.subTest(config=config):
                renderer = FormulaRenderer(config=config, adjtime="0\n0\nLOCAL\n")
                self.assertIn("chrony", renderer.state("chrony/init.sls"))
                self.assertIn("chrony_config", renderer.state("chrony/config.sls"))


class ConfigurationRenderTests(unittest.TestCase):
    def test_default_servers_and_optional_allow(self):
        for os_family, pool, options in (
            ("Debian", "debian", "offline minpoll 8"),
            ("RedHat", "centos", "iburst"),
        ):
            with self.subTest(os_family=os_family):
                directives = FormulaRenderer(os_family).directives()
                for index in range(4):
                    self.assertIn(f"server {index}.{pool}.pool.ntp.org {options}", directives)
                allowed = [line for line in directives if line.startswith("allow ")]
                self.assertEqual(
                    allowed,
                    ["allow 10/8", "allow 192.168/16", "allow 172.16/12"]
                    if os_family == "Debian" else [],
                )

    def test_empty_optional_lists_render_no_directives(self):
        renderer = FormulaRenderer(config={"ntpservers": [], "allow": [], "otherparams": []})
        self.assertEqual(renderer.directives(), [
            "keyfile /etc/chrony/chrony.keys",
            "driftfile /var/lib/chrony/chrony.drift",
            "logdir /var/log/chrony",
        ])

    def test_example_pillar_parses_and_renders(self):
        config = yaml.safe_load((ROOT / "pillar.example").read_text())["chrony"]["config"]
        self.assertEqual(config["driftfile"], "/var/lib/chrony/drift")
        renderer = FormulaRenderer(config=config, adjtime="0\n0\nLOCAL\n")
        directives = renderer.directives()
        self.assertIn("driftfile /var/lib/chrony/drift", directives)
        self.assertNotIn("rtconutc", directives)
        for server in config["ntpservers"]:
            self.assertIn(f"server {server} iburst", directives)
        for param in config["otherparams"]:
            self.assertIn(param, directives)
        self.assertIn("chrony", renderer.state("chrony/init.sls"))
        self.assertIn("chrony_config", renderer.state("chrony/config.sls"))


if __name__ == "__main__":
    unittest.main()
