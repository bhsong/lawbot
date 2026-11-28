"""Resolve the launchd scheduler's action and readiness budget without loading app settings."""

import argparse
from datetime import datetime, time
from pathlib import Path

from lawbot import envfile, settings

POLL_SECONDS = 5 * 60
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


def _schedule(now: time, hours: settings.ModelHours | None, deadline: time | None) -> tuple[str, int, str]:
    if hours is not None and not hours.contains(now):
        return 'stop', 0, ''

    current = _seconds(now)
    opening = _seconds(hours.start) if hours is not None else current
    elapsed = (current - opening) % DAY_SECONDS
    if deadline is not None:
        finish = _seconds(deadline)
        if hours is not None and hours.start > hours.end:
            # For an overnight window, a deadline earlier than opening belongs to the next day.
            remaining = (finish - opening) % DAY_SECONDS - elapsed
        else:
            remaining = finish - current
        return 'start', remaining, f'{deadline:%H:%M}'
    if hours is None:
        return 'start', 0, ''

    minute = ((opening + WARMUP_SECONDS) % DAY_SECONDS) // 60
    # Only the first scheduled tick after opening gets the full warmup period.
    remaining = WARMUP_SECONDS - elapsed if elapsed <= POLL_SECONDS else 0
    return 'start', remaining, f'{minute // 60:02}:{minute % 60:02}'


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
            value = args.hours if args.hours is not None else envfile.read_model_hours(Path('.env'))
            hours = settings.parse_model_hours(value)
        except ValueError:
            key = '--hours' if args.hours is not None else 'LAWBOT_MODEL_HOURS'
            print(f'Invalid {key}; use HH:MM-HH:MM.')
            return 1
        except OSError:
            print('Cannot read LAWBOT_MODEL_HOURS from .env.')
            return 1

    operation, remaining, label = _schedule(now, hours, deadline)
    print(operation, remaining, POLL_SECONDS, label)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
