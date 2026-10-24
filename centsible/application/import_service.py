"""Import bank or card statements into the journal.

Idempotency is the important property here.  Every statement line gets a
fingerprint derived from the account, date, amount and its external id (or, when
the export has none, the description).  Fingerprints are stored, so importing
the same file twice - or an overlapping export - imports nothing twice.

A bad line never aborts the run: it is reported with its line number and the
rest of the file is still imported.  That is the behaviour you want with a
2,000 line bank export where one row has an unparseable date.

把银行对账单或信用卡对账单导入日记账。

幂等性是这里最关键的性质。每一行对账单都会得到一个指纹，指纹由科目、日期、金额
以及该行的外部 ID 推导而来（导出文件中没有外部 ID 时，则改用描述）。指纹会被
保存下来，因此同一个文件导入两次——或者导入两份有重叠的导出——都不会重复导入
任何内容。

某一行有问题不会中断整个导入过程：它会带着行号被报告出来，文件其余部分仍会照常
导入。面对一份 2,000 行的银行导出，其中一行日期无法解析时，这正是你想要的行为。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from centsible.application.base import ServiceBase
from centsible.application.dto import ImportResult, ImportRowIssue
from centsible.application.ledger_service import LedgerService
from centsible.application.ports import (
    ImportBatch,
    MatchRule,
    StatementMapping,
    StatementReader,
    StatementRow,
)
from centsible.application.validators import require_postable_accounts
from centsible.domain.accounts import normalize_code
from centsible.domain.money import quantize
from centsible.domain.transactions import Posting, Transaction
from centsible.errors import CentsibleError, ValidationError

__all__ = ["ImportService", "StatementImportRequest", "file_checksum"]


@dataclass(frozen=True, slots=True)
class StatementImportRequest:
    """Everything needed to import one file.

    导入一个文件所需的全部信息。"""

    path: Path
    account_code: str
    counter_account: str
    mapping: StatementMapping
    source: str = "bank"
    dry_run: bool = False
    allow_future: bool = False
    keep_duplicate_lines: bool = False
    rules: tuple[MatchRule, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        object.__setattr__(self, "account_code", normalize_code(self.account_code))
        object.__setattr__(self, "counter_account", normalize_code(self.counter_account))
        object.__setattr__(self, "source", str(self.source or "bank").strip() or "bank")
        object.__setattr__(self, "rules", tuple(self.rules))
        if self.account_code == self.counter_account:
            raise ValidationError(
                "the statement account and the counter account must differ",
                field="counter_account",
            )
        for rule in self.rules:
            if rule.account_code == self.account_code:
                raise ValidationError(
                    f"match rule account {rule.account_code} is the statement "
                    "account itself",
                    field="rules",
                )


def file_checksum(path: Path) -> str:
    """SHA-256 of a file, used to recognise a re-imported statement.

    文件的 SHA-256，用于识别被重复导入的银行对账单。"""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ImportService(ServiceBase):
    """Turn statement rows into balanced journal entries.

    把银行对账单的行转换成借贷平衡的日记账分录。"""

    def __init__(
        self,
        uow_factory,
        clock,
        settings=None,
        *,
        reader: StatementReader,
        ledger: LedgerService | None = None,
    ) -> None:
        super().__init__(uow_factory, clock, settings)
        self._reader = reader
        self._ledger = ledger or LedgerService(uow_factory, clock, settings)

    # ------------------------------------------------------------------ import
    # -------------------------------------------------------------------- 导入
    def import_statement(self, request: StatementImportRequest) -> ImportResult:
        rows, parse_issues = self._reader.read(request.path, request.mapping)
        checksum = file_checksum(request.path)

        issues: list[ImportRowIssue] = [
            ImportRowIssue(line_number=issue.line_number, message=issue.message)
            for issue in parse_issues
        ]
        imported = 0
        duplicates = 0
        repeated = 0
        matched = 0
        failed = len(parse_issues)
        created_ids: list[int] = []
        batch_id: int | None = None
        rows_read = len(rows) + len(parse_issues)
        rule_usage: dict[str, int] = {}

        with self._transaction() as (uow, _owned):
            require_postable_accounts(
                uow,
                [
                    request.account_code,
                    request.counter_account,
                    *(rule.account_code for rule in request.rules),
                ],
            )

            if not request.dry_run:
                batch = uow.imports.add_batch(
                    ImportBatch(
                        source=request.source,
                        filename=request.path.name,
                        checksum=checksum,
                        imported_at=self._clock.now(),
                    )
                )
                batch_id = batch.id

            for row in rows:
                # The same reference twice in one file is usually a duplicated
                # export line - but sometimes it is two identical purchases on
                # the same day.  Skip it by default (and say so in the result),
                # import it when the caller asks for it.
                # 同一个文件里出现两次相同的引用号，通常是导出重复行——但有时确实是
                # 同一天两笔完全相同的消费。默认跳过（并在结果里说明），调用方明确
                # 要求时才导入。
                if row.occurrence > 1 and not request.keep_duplicate_lines:
                    repeated += 1
                    continue

                counter_account = request.counter_account
                rule = _match_rule(row, request.rules)
                if rule is not None:
                    counter_account = rule.account_code
                    matched += 1
                    usage_key = f"{rule.pattern} -> {rule.account_code}"
                    rule_usage[usage_key] = rule_usage.get(usage_key, 0) + 1

                fingerprint = _fingerprint(row, request, counter_account, rule)
                if uow.imports.has_fingerprint(fingerprint):
                    duplicates += 1
                    continue

                try:
                    transaction = self._build_transaction(
                        row, request, batch_id, counter_account
                    )
                except CentsibleError as exc:
                    failed += 1
                    issues.append(ImportRowIssue(line_number=row.line_number, message=str(exc)))
                    continue

                if request.dry_run:
                    imported += 1
                    continue

                try:
                    stored = self._ledger.record(
                        date=transaction.date,
                        description=transaction.description,
                        postings=transaction.postings,
                        payee=transaction.payee,
                        tags=transaction.tags,
                        import_batch=batch_id,
                        allow_future=request.allow_future,
                        uow=uow,
                    )
                except CentsibleError as exc:
                    failed += 1
                    issues.append(ImportRowIssue(line_number=row.line_number, message=str(exc)))
                    continue

                imported += 1
                if stored.id is not None:
                    created_ids.append(stored.id)
                    uow.imports.add_fingerprint(
                        fingerprint, batch_id=batch_id or 0, transaction_id=stored.id
                    )

            if batch_id is not None:
                uow.imports.finish_batch(
                    batch_id,
                    rows_read=rows_read,
                    rows_imported=imported,
                    rows_skipped=duplicates + repeated,
                )
                uow.audit.record(
                    action="statement.import",
                    entity="import_batch",
                    entity_id=batch_id,
                    detail=(
                        f"{request.path.name}: {imported} imported, "
                        f"{duplicates} duplicate(s), {repeated} repeated line(s), "
                        f"{failed} failed, {matched} matched by rule(s)"
                    ),
                )

        return ImportResult(
            source=request.source,
            filename=request.path.name,
            rows_read=rows_read,
            imported=imported,
            duplicates=duplicates,
            failed=failed,
            issues=tuple(issues),
            transaction_ids=tuple(created_ids),
            batch_id=batch_id,
            dry_run=request.dry_run,
            repeated=repeated,
            matched=matched,
            rule_usage=rule_usage,
        )

    # ------------------------------------------------------------------- query
    # -------------------------------------------------------------------- 查询
    def batches(self, limit: int = 10) -> list[ImportBatch]:
        with self._uow_factory() as uow:
            return uow.imports.recent_batches(limit)

    # ---------------------------------------------------------------- helpers
    # ------------------------------------------------------------------- 辅助
    def _build_transaction(
        self,
        row: StatementRow,
        request: StatementImportRequest,
        batch_id: int | None,
        counter_account: str,
    ) -> Transaction:
        amount = quantize(row.amount, self._settings.exponent)
        if amount != row.amount:
            raise ValidationError(
                f"amount {row.amount} has more decimal places than "
                f"{self._settings.currency} supports",
                field="amount",
            )
        return Transaction(
            date=row.date,
            description=row.description,
            postings=(
                Posting(request.account_code, amount),
                Posting(counter_account, -amount),
            ),
            payee=row.payee,
            import_batch=batch_id,
        )


def _match_rule(row: StatementRow, rules: tuple[MatchRule, ...]) -> MatchRule | None:
    """The first rule whose pattern appears in the description or the payee.

    第一个模式出现在描述或收款方中的规则。"""

    if not rules:
        return None
    haystack = f"{row.description} {row.payee}".lower()
    for rule in rules:
        if rule.pattern in haystack:
            return rule
    return None


def _fingerprint(
    row: StatementRow,
    request: StatementImportRequest,
    counter_account: str,
    rule: MatchRule | None,
) -> str:
    # Repeats inside one file must not share a fingerprint, or the second copy
    # would be dropped as a duplicate of the first one.
    # 同一文件内的重复行不能共用指纹，否则第二份会被当成第一份的重复而丢弃。
    reference = (
        row.external_id
        if row.occurrence == 1
        else f"{row.external_id}#{row.occurrence}"
    )
    parts = [
        request.account_code,
        row.date.isoformat(),
        f"{row.amount:f}",
        reference,
    ]
    if rule is not None:
        # Only rule-matched rows carry the resolved counter account in the
        # fingerprint: rows that fall back to the default counter keep the plain
        # fingerprint, so a file imported before the rules existed still
        # de-duplicates row for row.  Changing a rule, or pointing it at another
        # account, then re-imports exactly the affected rows into the new account.
        # 只有被规则命中的行才把解析出的对方科目写进指纹：回落到默认对方科目的行
        # 保持普通指纹，因此规则出现之前导入过的文件仍然逐行去重。修改规则或把它
        # 指向别的科目时，也只有受影响的那些行会重新导入到新科目。
        parts.append(f"cc={counter_account}")
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
