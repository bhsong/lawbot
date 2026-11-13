"""Resolve verified ERP rule editions at a KST and an independent business date."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from lawbot_common.erp_rules import (
    RULE_FEED_MAX_BYTES,
    ErpRules,
    ErpRuleScope,
    RuleSnapshot,
    RuleSnapshotScope,
    VerifiedErpRuleVersion,
    target_date,
)

KST = timezone(timedelta(hours=9))


def resolve(scope: RuleSnapshotScope, as_of: date, target: date) -> ErpRuleScope:
    target = target_date(target, scope.applicability)
    current = None
    scheduled = []
    recheck = False
    for edition in sorted(scope.editions, key=lambda entry: entry.version.appliesFrom):
        version = edition.version
        marks = [mark for mark in edition.reviews if mark.at.astimezone(KST).date() <= as_of]
        if not marks:
            continue
        mark = marks[-1]
        begins = max(version.effectiveFrom, version.appliesFrom)
        expired = version.appliesUntil is not None and target > version.appliesUntil
        if mark.decision == 'recheck_required' or (as_of >= begins and mark.at.astimezone(KST).date() < begins):
            recheck |= not expired
            continue
        verified = (
            None if expired else VerifiedErpRuleVersion(**version.model_dump(), verifiedAt=mark.at.astimezone(KST))
        )
        if as_of >= begins and target >= version.appliesFrom:
            current = verified
        elif verified:
            scheduled.append(verified)
    return ErpRuleScope(
        **scope.model_dump(exclude={'editions'}),
        targetOn=target,
        currentVersion=current,
        scheduledChanges=sorted(
            scheduled,
            key=lambda version: (
                max(version.effectiveFrom, version.appliesFrom),
                version.appliesFrom,
                version.ruleVersionId,
            ),
        ),
        recheckRequired=recheck,
    )


def today_kst() -> date:
    return datetime.now(KST).date()


def read(path: Path | None, as_of: date, target: date) -> ErpRules:
    if path is None:
        return ErpRules(state='unconfigured', asOfDate=as_of)
    try:
        with path.open('rb') as stream:
            data = stream.read(RULE_FEED_MAX_BYTES + 1)
        if len(data) > RULE_FEED_MAX_BYTES:
            return ErpRules(state='invalid', asOfDate=as_of)
        snapshot = RuleSnapshot.model_validate_json(data)
    except FileNotFoundError:
        return ErpRules(state='missing', asOfDate=as_of)
    except OSError, ValidationError:
        return ErpRules(state='invalid', asOfDate=as_of)
    return from_snapshot(snapshot, as_of, target)


def from_snapshot(snapshot: RuleSnapshot, as_of: date, target: date) -> ErpRules:
    return ErpRules.model_validate(
        {
            'state': 'ok',
            'generatedAt': snapshot.generatedAt,
            'asOfDate': as_of,
            'rules': [
                rule.model_dump(exclude={'scopes'})
                | {'scopes': [resolve(scope, as_of, target) for scope in rule.scopes]}
                for rule in snapshot.rules
            ],
        }
    )
