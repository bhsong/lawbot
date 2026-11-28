"""Resolve the launchd scheduler's action and readiness budget without loading app settings."""

import argparse
from contextlib import suppress
from datetime import datetime, time
from pathlib import Path

from lawbot import envfile, settings

CALENDAR_SECONDS = 30 * 60
WARMUP_SECONDS = 25 * 60
DAY_SECONDS = 24 * 60 * 60


def _clock(value: str, *, seconds: bool = False) -> time:
    clock = time.fromisoformat(value)
    if clock.tzinfo is not None or (
        value != clock.isoformat(timespec='minutes') and not (seconds and value == clock.isoformat(timespec='seconds'))
    ):
        raise ValueError
    return clock


def _seconds(clock: time) -> int:
    return (clock.hour * 60 + clock.minute) * 60 + clock.second


def _schedule(
    action: str, now: time, hours: settings.ModelHours | None, deadline: time | None
) -> tuple[str, int, int, str]:
    if hours is not None and not hours.contains(now):
        return 'stop', 0, 0, ''

    current = _seconds(now)
    remaining, label = 0, ''
    if deadline is not None:
        finish = _seconds(deadline)
        remaining = finish - current
        if hours is not None and hours.start > hours.end:
            opening = _seconds(hours.start)
            remaining = (finish - opening) % DAY_SECONDS - (current - opening) % DAY_SECONDS
        label = f'{deadline:%H:%M}'
    elif hours is not None:
        opening = _seconds(hours.start)
        elapsed = (current - opening) % DAY_SECONDS
        first_tick = -opening % CALENDAR_SECONDS
        # Launchd uses the same invocation for RunAtLoad and calendar ticks.
        if first_tick <= elapsed < first_tick + 60:
            remaining = WARMUP_SECONDS - elapsed
        minute = ((opening + WARMUP_SECONDS) % DAY_SECONDS) // 60
        label = f'{minute // 60:02}:{minute % 60:02}'

    repeat = remaining > 0
    budget = remaining if repeat else 30
    if action == 'apply':
        # Leave time for the probe's 1 s polling and 1 s termination grace.
        before_tick = CALENDAR_SECONDS - current % CALENDAR_SECONDS - 3
        if budget > before_tick:
            budget = max(0, before_tick)
            if repeat:
                finish = (current + budget) % DAY_SECONDS
                label = f'{finish // 3600:02}:{finish // 60 % 60:02}:{finish % 60:02}'
    return 'start', budget, int(repeat), label


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('apply', 'start'))
    parser.add_argument('--hours')
    parser.add_argument('--deadline')
    parser.add_argument('--now')
    args = parser.parse_args()

    try:
        now = _clock(args.now, seconds=True) if args.now else datetime.now().time()
    except ValueError:
        print('Invalid local time; use HH:MM[:SS] for --now.')
        return 1
    try:
        deadline = _clock(args.deadline) if args.deadline is not None else None
    except ValueError:
        print('Invalid --deadline; use HH:MM.')
        return 1
    hours = None
    if args.action == 'apply':
        try:
            value = args.hours
            if value is None:
                with suppress(FileNotFoundError):
                    value = envfile.read_model_hours(Path('.env'))
            hours = settings.parse_model_hours(value)
        except ValueError:
            key = '--hours' if args.hours is not None else 'LAWBOT_MODEL_HOURS'
            print(f'Invalid {key}; use HH:MM-HH:MM.')
            return 1
        except OSError:
            print('Cannot read LAWBOT_MODEL_HOURS from .env.')
            return 1

    print(*_schedule(args.action, now, hours, deadline))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
