"""Tests for configuration loading, validation and serialisation.

配置加载、校验与序列化的测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from centsible.config import (
    CONFIG_FILENAME,
    DEFAULT_DATABASE,
    ENV_CURRENCY,
    ENV_DATABASE,
    ENV_LOG_LEVEL,
    Config,
)
from centsible.errors import ConfigurationError

CLEAN_ENV = {
    key: value
    for key, value in os.environ.items()
    if key not in {ENV_DATABASE, ENV_CURRENCY, ENV_LOG_LEVEL, "CENTSIBLE_CONFIG"}
}


class ConfigDefaultsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = Config()
        self.assertEqual(config.currency, "CNY")
        self.assertEqual(config.fiscal_year_start_month, 1)
        self.assertEqual(config.log_level, "INFO")
        self.assertEqual(config.database_path, DEFAULT_DATABASE)
        self.assertIsNone(config.source)
        self.assertEqual(config.currency_exponent, 2)
        # The command line is the default front end; the window is opt-in.
        # 命令行是默认前端；窗口需要显式开启。
        self.assertTrue(config.cli_enabled)
        self.assertFalse(config.gui_enabled)

    def test_validation_rejects_bad_values(self) -> None:
        with self.assertRaises(ConfigurationError):
            Config(currency="EURO")
        with self.assertRaises(ConfigurationError):
            Config(currency="RMB¥")
        with self.assertRaises(ConfigurationError):
            Config(fiscal_year_start_month=13)
        with self.assertRaises(ConfigurationError):
            Config(fiscal_year_start_month=True)  # type: ignore[arg-type]
        with self.assertRaises(ConfigurationError):
            Config(log_level="LOUD")
        with self.assertRaises(ConfigurationError):
            Config(date_format="%Y-%m")
        with self.assertRaises(ConfigurationError):
            Config(cli_enabled=1)  # type: ignore[arg-type]
        with self.assertRaises(ConfigurationError):
            Config(gui_enabled="yes")  # type: ignore[arg-type]

    def test_at_least_one_interface_must_stay_enabled(self) -> None:
        with self.assertRaises(ConfigurationError) as caught:
            Config(cli_enabled=False, gui_enabled=False)
        self.assertIn("at least one interface", str(caught.exception))

    def test_gui_only_is_allowed(self) -> None:
        config = Config(cli_enabled=False, gui_enabled=True)
        self.assertFalse(config.cli_enabled)
        self.assertTrue(config.gui_enabled)

    def test_level_is_upper_cased(self) -> None:
        self.assertEqual(Config(log_level="debug").log_level, "DEBUG")

    def test_overrides_are_validated(self) -> None:
        config = Config()
        self.assertEqual(config.with_overrides(currency="usd").currency, "USD")
        with self.assertRaises(ConfigurationError):
            config.with_overrides(currency="nope")

    def test_as_mapping(self) -> None:
        mapping = Config().as_mapping()
        self.assertEqual(mapping["ledger"]["currency"], "CNY")
        self.assertEqual(mapping["database"]["path"], str(DEFAULT_DATABASE))
        self.assertIsNone(mapping["source"])


class ConfigFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.directory = Path(self._tempdir.name)
        patcher = mock.patch.dict(os.environ, CLEAN_ENV, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, text: str, name: str = CONFIG_FILENAME) -> Path:
        path = self.directory / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_every_section(self) -> None:
        path = self.write(
            """
[database]
path = "ledger.db"

[ledger]
currency = "USD"
fiscal_year_start_month = 4

[logging]
level = "DEBUG"
file = "logs/centsible.log"

[format]
date = "%d/%m/%Y"
"""
        )
        config = Config.load(path)
        self.assertEqual(config.currency, "USD")
        self.assertEqual(config.fiscal_year_start_month, 4)
        self.assertEqual(config.log_level, "DEBUG")
        self.assertEqual(config.log_file, Path("logs/centsible.log"))
        self.assertEqual(config.date_format, "%d/%m/%Y")
        self.assertEqual(config.source, path)

    def test_relative_paths_resolve_against_the_config_file(self) -> None:
        path = self.write('[database]\npath = "data/ledger.db"\n')
        config = Config.load(path)
        self.assertEqual(
            config.resolved_database_path(), (self.directory / "data" / "ledger.db").resolve()
        )
        self.assertEqual(
            Config(
                database_path=Path("x.db"), log_file=Path("l.log"), source=self.directory / "c.toml"
            ).resolved_log_file(),
            (self.directory / "l.log").resolve(),
        )

    def test_absolute_paths_are_kept(self) -> None:
        absolute = self.directory / "abs.db"
        path = self.write(f'[database]\npath = "{absolute.as_posix()}"\n')
        self.assertEqual(Config.load(path).resolved_database_path(), absolute.resolve())

    def test_missing_explicit_file(self) -> None:
        with self.assertRaises(ConfigurationError):
            Config.load(self.directory / "nope.toml")

    def test_unknown_section_is_rejected(self) -> None:
        path = self.write("[colours]\ntheme = 'dark'\n")
        with self.assertRaises(ConfigurationError) as caught:
            Config.load(path)
        self.assertIn("unknown configuration section", str(caught.exception))

    def test_unknown_key_is_rejected(self) -> None:
        path = self.write("[ledger]\ncurrancy = 'USD'\n")
        with self.assertRaises(ConfigurationError) as caught:
            Config.load(path)
        self.assertIn("unknown key", str(caught.exception))

    def test_section_must_be_a_table(self) -> None:
        path = self.write("ledger = 3\n")
        with self.assertRaises(ConfigurationError):
            Config.load(path)

    def test_interface_switches_are_parsed(self) -> None:
        path = self.write("[interface]\ncli = false\ngui = true\n")
        config = Config.load(path)
        self.assertFalse(config.cli_enabled)
        self.assertTrue(config.gui_enabled)

    def test_interface_rejects_unknown_keys_and_bad_values(self) -> None:
        path = self.write("[interface]\nweb = true\n")
        with self.assertRaises(ConfigurationError) as caught:
            Config.load(path)
        self.assertIn("unknown key", str(caught.exception))

        path = self.write('[interface]\ngui = "yes"\n')
        with self.assertRaises(ConfigurationError) as caught:
            Config.load(path)
        self.assertIn("must be true or false", str(caught.exception))

    def test_interface_cannot_disable_both_front_ends(self) -> None:
        path = self.write("[interface]\ncli = false\ngui = false\n")
        with self.assertRaises(ConfigurationError) as caught:
            Config.load(path)
        self.assertIn("at least one interface", str(caught.exception))

    def test_interface_round_trips_through_serialisation(self) -> None:
        config = Config(cli_enabled=False, gui_enabled=True)
        self.assertEqual(
            config.as_mapping()["interface"], {"cli": False, "gui": True}
        )
        toml = config.to_toml()
        self.assertIn("[interface]", toml)
        self.assertIn("cli = false", toml)
        self.assertIn("gui = true", toml)

    def test_invalid_toml(self) -> None:
        path = self.write("this is not toml")
        with self.assertRaises(ConfigurationError) as caught:
            Config.load(path)
        self.assertIn("invalid TOML", str(caught.exception))

    def test_environment_overrides_the_file(self) -> None:
        path = self.write('[ledger]\ncurrency = "USD"\n')
        with mock.patch.dict(
            os.environ,
            {ENV_CURRENCY: "EUR", ENV_DATABASE: "env.db", ENV_LOG_LEVEL: "ERROR"},
            clear=True,
        ):
            config = Config.load(path)
        self.assertEqual(config.currency, "EUR")
        self.assertEqual(config.database_path, Path("env.db"))
        self.assertEqual(config.log_level, "ERROR")
        self.assertEqual(config.resolved_database_path(), (self.directory / "env.db").resolve())

    def test_environment_only(self) -> None:
        config_file = self.directory / "missing.toml"
        with mock.patch.dict(os.environ, {ENV_CURRENCY: "JPY"}, clear=True):
            config = Config.load(config_file) if config_file.exists() else None
        self.assertIsNone(config)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Config.load().currency, "CNY")

    def test_toml_round_trip(self) -> None:
        original = Config(
            currency="EUR",
            fiscal_year_start_month=7,
            log_level="DEBUG",
            log_file=Path("logs/app.log"),
            date_format="%d/%m/%Y",
        )
        path = self.write(original.to_toml())
        loaded = Config.load(path)
        self.assertEqual(loaded.currency, original.currency)
        self.assertEqual(loaded.fiscal_year_start_month, 7)
        self.assertEqual(loaded.log_level, "DEBUG")
        self.assertEqual(loaded.log_file, Path("logs/app.log"))
        self.assertEqual(loaded.date_format, "%d/%m/%Y")

    def test_to_toml_is_comment_annotated(self) -> None:
        text = Config().to_toml()
        self.assertIn("[database]", text)
        self.assertIn("[ledger]", text)
        self.assertIn("currency = \"CNY\"", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
