# Образец: скрипт со своим тестом (tools/<имя>.py + tools/tests/test_<имя>.py)

Эталон — `tools/token_check.py` (функция `main`) и `tools/tests/test_token_check.py` (класс
`TokenCheckTest`). Скрипты в `plugins/supertick/scripts/` — наш код линии 0.14–0.17, в 0.18.0
перенесены побайтно. В 0.18.1 два из них изменены: `supervisor-tick.py` (`task_id` рядом с
`read_mail`, вердикт приёмки по первой непустой строке) и `team-wait.sh` (`head_value`,
`task_id`). В 0.19.0 снова изменён `supervisor-tick.py` — порог `engine_result_unread` у прокси
(функция `engine_unread_sec`, константа `ENGINE_UNREAD_PROXY_SEC`), а у `team-wait.sh` правлена
только шапка-комментарий; остальные по-прежнему побайтные. Правка такого скрипта — только вместе с тестом в
`tools/tests`. Их `scripts/tests` по-прежнему держит ровно 50 тестов и не правится — новые тесты
только в `tools/tests`.

```python
#!/usr/bin/env python3
"""<Что проверяет, одной фразой.>

  <имя>.py --spec-dir <каталог> [--plugin <каталог плагина>]

Код выхода: 0 — расхождений нет, 1 — есть, 2 — ошибка аргументов.
"""
import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]          # от файла, не от текущего каталога


def main(argv=None):
    ap = argparse.ArgumentParser(description="<что проверяет>")
    ap.add_argument("--spec-dir", help="<обязательный источник вне репозитория>")
    a = ap.parse_args(argv)
    if not a.spec_dir:                               # нет входа — код 2, а не тихий PASS
        ap.error("укажите путь: --spec-dir <каталог>")
    problems = []
    ...
    print(f"ИТОГ: проверок={...} расхождений={len(problems)}")
    return 1 if problems else 0
```

Тест: модуль грузится из файла (`importlib.util.spec_from_file_location`), данные —
синтетика во `tempfile.TemporaryDirectory()`, на каждую известную порчу свой `test_…`, плюс
вызов без обязательного аргумента через `subprocess` с проверкой ненулевого кода. Авторский
корпус в тесты и в CI не попадает.

Bash-обёртка (`tools/<имя>.sh`) тестируется так же — через `subprocess`, по коду выхода и
выводу. Эталон — `tools/tests/test_run_overlap.py`, класс `RunOverlap`: корпус — синтетический
git-репозиторий во `tempfile.TemporaryDirectory()` вне рабочего дерева (свой репозиторий, а не
подкаталог нашего, иначе `git -C` найдёт наш HEAD); git зовётся с `-c user.name=… -c user.email=…`
(константа `GIT_ID`), чтобы не зависеть от глобального конфига; из окружения убраны `GIT_DIR`,
`GIT_WORK_TREE`, `GIT_INDEX_FILE` (константа `ENV`) — под git-хуком они указывали бы на наш
репозиторий. На каждую ветку обёртки свой тест: корпус в git
(`test_corpus_in_git_reports_its_head`), без git с явным SHA
(`test_corpus_without_git_takes_explicit_sha`), без git и без SHA
(`test_corpus_without_git_and_without_sha_fails`), явный SHA расходится с HEAD
(`test_corpus_in_git_with_other_sha_fails`).

Правка скрипта из `plugins/supertick/scripts/` — эталон `tools/tests/test_tick_parsers.py`
(0.18.1, прогон feature-trial-medium): тик грузится из файла, `team-wait.sh` зовётся через
`subprocess` (класс `Run`, метод `wait`); каждый вызов team-wait — в своём каталоге прогона
(`.seen` гасит повтор) и с `--max 2`. Одно правило в двух скриптах сверяется одним тестом на
общей таблице значений (`test_tick_and_team_wait_agree_on_the_task_id`). Новое правило — свой
класс (`TolerantHead`), неизменное поведение — отдельный (`CurrentBehaviour`). Порча для
проверки теста — на копии скрипта (`git show HEAD:<путь>` во временный каталог), не в дереве.

Порог, который зависит от состояния каталога прогона, — эталон `tools/tests/test_tick_proxy_unread.py`
(0.19.0, прогон feature-trial-fixes): класс `Run` собирает во `tempfile` каталог с одним
досчитанным вызовом движка и задаёт возраст `.done`, метод `card` кладёт карточку роли
(статус, `lastEventAt` и mtime относительно `.done`), метод `unread` спрашивает тик. Классы —
по ветке правила: `ProxyThreshold` (3 минуты, карточка старше `.done`), `ProxyReadTheResult`
(карточка новее — 15 минут), `NoProxyCard` (карточки нет или она `stopped` — 15 минут). Тест
проверен на старом тике (1779ed1) и на тике без учёта mtime карточки — оба падают.
