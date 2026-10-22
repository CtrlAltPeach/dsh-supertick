"""Порог engine_result_unread в тике: 3 минуты у прокси, пока он не отметил чтение карточкой, иначе 15.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
Прокси — роль, у которой рядом с engine/<роль>/ есть карточка runs/<роль>.json не в статусе
done/stopped. Время карточки — позднее из lastEventAt и mtime файла; новее `.done` — значит,
прокси уже прочитал result.md (note="ENGINE READ") и разбирает его, порог снова 15 минут.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "plugins" / "supertick" / "scripts"
spec = importlib.util.spec_from_file_location("supervisor_tick_proxy_unread", SCRIPTS / "supervisor-tick.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
ROLE = "architect-systems"


class Run:
    """Каталог прогона с одним досчитанным, но не забранным вызовом движка `ROLE`."""

    def __init__(self, case, done_min):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dir = self.root / ".claude" / "teams" / "feature-x"
        engine = self.dir / "engine" / ROLE
        engine.mkdir(parents=True)
        (self.dir / "runs").mkdir()
        (engine / "001.out.pid").write_text("0\n", encoding="utf-8")
        done = engine / "001.out.done"
        done.write_text("status=ok\n", encoding="utf-8")
        self.done_at = NOW - timedelta(minutes=done_min)
        os.utime(done, (self.done_at.timestamp(), self.done_at.timestamp()))

    def card(self, status="running", after_done_sec=-60, mtime_after_done_sec=None):
        """Карточка прокси: lastEventAt — `.done` плюс `after_done_sec`, mtime — так же или своё."""
        at = self.done_at + timedelta(seconds=after_done_sec)
        mtime = at if mtime_after_done_sec is None else self.done_at + timedelta(seconds=mtime_after_done_sec)
        path = self.dir / "runs" / f"{ROLE}.json"
        path.write_text(json.dumps({"name": ROLE, "role": ROLE, "status": status, "note": "ENGINE WAIT",
                                    "spawnedAt": (at - timedelta(hours=1)).isoformat(),
                                    "startedAt": (at - timedelta(hours=1)).isoformat(),
                                    "lastEventAt": at.isoformat()}), encoding="utf-8")
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))

    def unread(self):
        return [(a["priority"], a["run"]) for a in st.tick(self.dir, self.root, NOW)["actions"]
                if a["kind"] == "engine_result_unread"]


class ProxyThreshold(unittest.TestCase):
    """Прокси с живой карточкой, ещё не отметивший чтение: P0 с трёх минут."""

    def test_proxy_four_minutes_card_older_than_done_is_p0(self):
        r = Run(self, 4)
        r.card(after_done_sec=-60)
        self.assertEqual(r.unread(), [("P0", ROLE)])

    def test_proxy_two_minutes_is_quiet(self):
        r = Run(self, 2)
        r.card(after_done_sec=-60)
        self.assertEqual(r.unread(), [])


class ProxyReadTheResult(unittest.TestCase):
    """Карточка новее `.done` — прокси прочитал result.md и разбирает: снова 15 минут."""

    def test_proxy_four_minutes_card_newer_than_done_is_quiet(self):
        r = Run(self, 4)
        r.card(after_done_sec=30)
        self.assertEqual(r.unread(), [])

    def test_card_mtime_newer_than_done_counts_when_last_event_is_older(self):
        r = Run(self, 4)   # роль без Bash написала lastEventAt руками и старым, файл новее
        r.card(after_done_sec=-60, mtime_after_done_sec=30)
        self.assertEqual(r.unread(), [])

    def test_proxy_sixteen_minutes_card_newer_than_done_is_p0(self):
        r = Run(self, 16)
        r.card(after_done_sec=30)
        self.assertEqual(r.unread(), [("P0", ROLE)])


class NoProxyCard(unittest.TestCase):
    """Без карточки (одноразовая роль ведущего) и с карточкой stopped — 15 минут, как раньше."""

    def test_no_card_four_minutes_is_quiet(self):
        r = Run(self, 4)
        self.assertEqual(r.unread(), [])

    def test_no_card_sixteen_minutes_is_p0(self):
        r = Run(self, 16)
        self.assertEqual(r.unread(), [("P0", ROLE)])

    def test_stopped_card_four_minutes_is_quiet(self):
        r = Run(self, 4)
        r.card(status="stopped", after_done_sec=-60)
        self.assertEqual(r.unread(), [])


if __name__ == "__main__":
    unittest.main()
