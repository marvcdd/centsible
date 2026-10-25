"""Configuration loading and validation for Centsible.

Resolution order (later wins):

1. built-in defaults;
2. a TOML file - ``--config PATH``, else ``$CENTSIBLE_CONFIG``, else
   ``./centsible.toml`` when that file exists;
3. environment variables ``CENTSIBLE_DB``, ``CENTSIBLE_CURRENCY`` and
   ``CENTSIBLE_LOG_LEVEL``.

The file format is intentionally strict: an unknown section or key is a
configuration error rather than a silently ignored typo.  Relative paths inside
a configuration file are resolved against the directory that file lives in, so
a ledger works no matter which directory you invoke it from.

Centsible 的配置加载与校验。

解析顺序（后者覆盖前者）：

1. 内置默认值；
2. TOML 文件 —— 优先 ``--config PATH``，其次 ``$CENTSIBLE_CONFIG``；两者都未给出
   时，若 ``./centsible.toml`` 存在则使用该文件；
3. 环境变量 ``CENTSIBLE_DB``、``CENTSIBLE_CURRENCY`` 与
   ``CENTSIBLE_LOG_LEVEL``。

文件格式刻意保持严格：出现未知的节或键即视为配置错误，而不是被静默忽略的拼写
错误。配置文件中的相对路径以该文件所在目录为基准解析，因此无论从哪个目录调用，
账簿都能正常工作。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from centsible.domain.money import DEFAULT_CURRENCY, currency_exponent, normalize_currency
from centsible.errors import ConfigurationError, ValidationError

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_DATABASE",
    "ENV_CONFIG",
    "ENV_CURRENCY",
    "ENV_DATABASE",
    "ENV_LOG_LEVEL",
    "LOG_LEVELS",
    "Config",
]

CONFIG_FILENAME = "centsible.toml"
DEFAULT_DATABASE = Path("data") / "ledger.db"
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

ENV_CONFIG = "CENTSIBLE_CONFIG"
ENV_DATABASE = "CENTSIBLE_DB"
ENV_CURRENCY = "CENTSIBLE_CURRENCY"
ENV_LOG_LEVEL = "CENTSIBLE_LOG_LEVEL"

_SECTIONS: dict[str, frozenset[str]] = {
    "database": frozenset({"path"}),
    "ledger": frozenset({"currency", "fiscal_year_start_month"}),
    "logging": frozenset({"level", "file"}),
    "format": frozenset({"date"}),
    "interface": frozenset({"cli", "gui"}),
}


@dataclass(frozen=True, slots=True)
class Config:
    """Effective, validated configuration of one ledger database.

    单个账簿数据库生效且已通过校验的配置。"""

    database_path: Path = DEFAULT_DATABASE
    currency: str = DEFAULT_CURRENCY
    fiscal_year_start_month: int = 1
    date_format: str = "%Y-%m-%d"
    log_level: str = "INFO"
    log_file: Path | None = None
    source: Path | None = None
    cli_enabled: bool = True
    gui_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "database_path", Path(str(self.database_path)))
        if self.log_file is not None:
            object.__setattr__(self, "log_file", Path(str(self.log_file)))
        if self.source is not None:
            object.__setattr__(self, "source", Path(str(self.source)))
        try:
            object.__setattr__(self, "currency", normalize_currency(self.currency))
        except ValidationError as exc:
            raise ConfigurationError(str(exc), value=self.currency) from exc

        month = self.fiscal_year_start_month
        if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
            raise ConfigurationError(
                "ledger.fiscal_year_start_month must be an integer between 1 and 12",
                value=month,
            )

        level = str(self.log_level).strip().upper()
        if level not in LOG_LEVELS:
            raise ConfigurationError(
                f"logging.level must be one of {', '.join(LOG_LEVELS)}",
                value=self.log_level,
            )
        object.__setattr__(self, "log_level", level)

        date_format = str(self.date_format)
        if not all(token in date_format for token in ("%Y", "%m", "%d")):
            raise ConfigurationError(
                "format.date must describe a full date containing %Y, %m and %d",
                value=date_format,
            )
        object.__setattr__(self, "date_format", date_format)

        # Interface switches: the ledger needs at least one way in.  Booleans
        # only - an int or a string here is a configuration error, not a value.
        # 界面开关：账本至少需要一种入口。只接受布尔值——这里出现整数或字符串
        # 属于配置错误，而不是一个合法取值。
        for field in ("cli_enabled", "gui_enabled"):
            value = getattr(self, field)
            if not isinstance(value, bool):
                raise ConfigurationError(
                    f"interface.{field.replace('_enabled', '')} must be true or false",
                    value=value,
                )
        if not self.cli_enabled and not self.gui_enabled:
            raise ConfigurationError(
                "at least one interface must be enabled: set interface.cli "
                "and/or interface.gui to true"
            )

    # ------------------------------------------------------------------ loads
    # ------------------------------------------------------------------ 加载
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """Build a configuration, reading an optional TOML file.

        A relative ``database.path`` inside the file is resolved against the
        directory containing that file; a path supplied on the command line or
        through an environment variable is not (the CLI resolves it against the
        current directory).

        构建一份配置，并读取可选的 TOML 文件。

        文件中相对的 ``database.path`` 以该文件所在目录为基准解析；通过命令行
        或环境变量给出的路径则不然（CLI 会以当前目录为基准解析）。
        """

        explicit = Path(path) if path is not None else None
        env_path = os.environ.get(ENV_CONFIG)
        if explicit is not None:
            candidate, required = explicit, True
        elif env_path:
            candidate, required = Path(env_path), True
        else:
            candidate, required = Path(CONFIG_FILENAME), False

        if not candidate.exists():
            if required:
                raise ConfigurationError(f"configuration file not found: {candidate}")
            return _apply_environment(cls())

        config = cls._from_mapping(_read_toml(candidate), source=candidate)
        return _apply_environment(config)

    @classmethod
    def _from_mapping(cls, data: Mapping[str, Any], *, source: Path | None) -> "Config":
        unknown_sections = sorted(set(data) - set(_SECTIONS))
        if unknown_sections:
            raise ConfigurationError(
                "unknown configuration section(s): "
                + ", ".join(unknown_sections)
                + f"; known sections are: {', '.join(sorted(_SECTIONS))}",
                source=str(source) if source else None,
            )

        values: dict[str, Any] = {}
        for section, allowed in _SECTIONS.items():
            payload = data.get(section, {})
            if not isinstance(payload, Mapping):
                raise ConfigurationError(f"[{section}] must be a table")
            unknown = sorted(set(payload) - allowed)
            if unknown:
                raise ConfigurationError(
                    f"unknown key(s) in [{section}]: {', '.join(unknown)}; "
                    f"allowed keys are: {', '.join(sorted(allowed))}"
                )
            values.update(_map_section(section, payload))

        values["source"] = source
        return cls(**values)

    # --------------------------------------------------------------- computed
    # --------------------------------------------------------------- 计算属性
    @property
    def currency_exponent(self) -> int:
        """Number of decimal places the ledger currency uses.

        账簿币种使用的小数位数。"""

        return currency_exponent(self.currency)

    def resolved_database_path(self) -> Path:
        """Absolute path of the SQLite file, honouring the config location.

        SQLite 文件的绝对路径，解析时以配置文件所在位置为准。"""

        path = self.database_path
        if path.is_absolute():
            # Resolving an absolute path is not a no-op on Windows: CI runners
            # spell their TEMP directory with an 8.3 short name such as
            # ``RUNNER~1``, and the same file must not have two spellings.
            # 在 Windows 上解析绝对路径并非空操作：CI 运行器会把 TEMP 目录写成
            # ``RUNNER~1`` 这样的 8.3 短名，同一个文件不能有两种写法。
            return path.resolve()
        base = self.source.parent if self.source is not None else Path.cwd()
        return (base / path).resolve()

    def resolved_log_file(self) -> Path | None:
        if self.log_file is None:
            return None
        path = self.log_file
        if path.is_absolute():
            # Same short-name normalisation as the database path.
            # 与数据库路径相同的短名规范化。
            return path.resolve()
        base = self.source.parent if self.source is not None else Path.cwd()
        return (base / path).resolve()

    def with_overrides(self, **changes: Any) -> "Config":
        """Return a copy with the given fields replaced (and re-validated).

        返回一份副本，其中给定字段被替换（并重新校验）。"""

        return replace(self, **changes)

    def as_mapping(self) -> dict[str, Any]:
        """Nested, JSON friendly view - used by ``centsible config show``.

        嵌套的、便于 JSON 序列化的视图 —— 供 ``centsible config show`` 使用。"""

        return {
            "database": {"path": str(self.database_path)},
            "ledger": {
                "currency": self.currency,
                "fiscal_year_start_month": self.fiscal_year_start_month,
            },
            "logging": {
                "level": self.log_level,
                "file": str(self.log_file) if self.log_file else None,
            },
            "format": {"date": self.date_format},
            "interface": {"cli": self.cli_enabled, "gui": self.gui_enabled},
            "source": str(self.source) if self.source else None,
            "database_path_resolved": str(self.resolved_database_path()),
        }

    def to_toml(self) -> str:
        """Serialise the configuration back to TOML (used by ``init``).

        Paths are written with forward slashes: valid on every platform and
        easier to read than TOML's escaped backslashes.

        把配置序列化回 TOML（由 ``init`` 使用）。

        路径使用正斜杠写出：在每个平台上都合法，也比 TOML 里转义的反斜杠更易读。
        """

        lines = [
            "# Centsible configuration",
            "# Every key below is optional; the values shown are the defaults.",
            "",
            "[database]",
            f'path = {_toml_string(self.database_path.as_posix())}',
            "",
            "[ledger]",
            f'currency = {_toml_string(self.currency)}',
            f"fiscal_year_start_month = {self.fiscal_year_start_month}",
            "",
            "[logging]",
            f'level = {_toml_string(self.log_level)}',
        ]
        if self.log_file is not None:
            lines.append(f'file = {_toml_string(self.log_file.as_posix())}')
        else:
            lines.append('# file = "logs/centsible.log"')
        lines += [
            "",
            "[format]",
            f'date = {_toml_string(self.date_format)}',
            "",
            "[interface]",
            f"cli = {str(self.cli_enabled).lower()}",
            f"gui = {str(self.gui_enabled).lower()}",
            "",
        ]
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover - display helper | 显示辅助函数
        return (
            f"currency={self.currency} database={self.database_path} "
            f"fiscal_year_start={self.fiscal_year_start_month}"
        )


# --------------------------------------------------------------------- helpers
# --------------------------------------------------------------------- 辅助函数
def _map_section(section: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if section == "database":
        values: dict[str, Any] = {}
        if "path" in payload:
            values["database_path"] = _expect_string(payload["path"], "database.path")
        return values

    if section == "ledger":
        values = {}
        if "currency" in payload:
            values["currency"] = _expect_string(payload["currency"], "ledger.currency")
        if "fiscal_year_start_month" in payload:
            values["fiscal_year_start_month"] = _expect_int(
                payload["fiscal_year_start_month"], "ledger.fiscal_year_start_month"
            )
        return values

    if section == "logging":
        values = {}
        if "level" in payload:
            values["log_level"] = _expect_string(payload["level"], "logging.level")
        if "file" in payload and payload["file"] is not None:
            values["log_file"] = Path(_expect_string(payload["file"], "logging.file"))
        return values

    if section == "format":
        values = {}
        if "date" in payload:
            values["date_format"] = _expect_string(payload["date"], "format.date")
        return values

    if section == "interface":
        values = {}
        if "cli" in payload:
            values["cli_enabled"] = _expect_bool(payload["cli"], "interface.cli")
        if "gui" in payload:
            values["gui_enabled"] = _expect_bool(payload["gui"], "interface.gui")
        return values

    raise AssertionError(f"unhandled configuration section {section!r}")  # pragma: no cover | 未覆盖分支


def _read_toml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:  # pragma: no cover - guarded by callers | 由调用方兜底
        raise ConfigurationError(f"configuration file not found: {path}") from exc
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {path}: {exc}") from exc


def _apply_environment(config: Config) -> Config:
    """Apply ``CENTSIBLE_*`` overrides on top of the file (and defaults).

    在文件配置（与默认值）之上应用 ``CENTSIBLE_*`` 覆盖项。"""

    changes: dict[str, Any] = {}

    if raw_path := os.environ.get(ENV_DATABASE):
        changes["database_path"] = Path(raw_path)
    if raw_currency := os.environ.get(ENV_CURRENCY):
        changes["currency"] = raw_currency
    if raw_level := os.environ.get(ENV_LOG_LEVEL):
        changes["log_level"] = raw_level

    return config.with_overrides(**changes) if changes else config


def _expect_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field} must be a non-empty string", value=value)
    return value.strip()


def _expect_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{field} must be an integer", value=value)
    return value


def _expect_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{field} must be true or false", value=value)
    return value


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
