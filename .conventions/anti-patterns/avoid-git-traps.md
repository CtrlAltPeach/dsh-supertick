# Ловушки git при коммите своих путей

Три вещи, воспроизведённые в этом прогоне (риски R3, R4 и правило общего индекса).

1. **Бит x только в индексе.** `git add --chmod=+x f` при `644` на диске, затем `git commit -- f`
   коммитит `100644`: коммит по путям берёт файл с диска.
2. **Коммит по путям с новым файлом.** `git commit -F msg -- новый/файл` без `git add` падает
   «pathspec did not match».
3. **Общий индекс.** Параллельные кодеры делят один индекс: `git add .`/`-A` заберёт чужую работу.

Плохо:

```bash
git add --chmod=+x tools/run_overlap.sh
git add -A && git commit -m "задача 16"
```

Хорошо:

```bash
install -m 755 src.sh tools/run_overlap.sh            # бит на диске
git add -- tools/run_overlap.sh tools/tests/test_x.py
git commit -F "$MSG" -- tools/run_overlap.sh tools/tests/test_x.py
git ls-tree -r HEAD tools | grep run_overlap          # проверять HEAD, а не индекс
```

Почему: проверка индекса показывает то, чего в коммите нет. `index.lock` занят — подождать 2 с
и повторить, ничего не удалять.

# Порча для проверки теста — прямо в общем рабочем дереве

Проверяя, ловит ли тест регрессию, порчу кладут в рабочий файл, а потом возвращают его
`git checkout --`. Если в файле была незакоммиченная чужая правка, она уходит вместе с порчей.

Плохо:

```bash
sed -i 's/"\$SHA"/ffc350e/' tools/run_overlap.sh
python3 -m unittest discover -s tools/tests -p test_run_overlap.py
git checkout -- tools/run_overlap.sh                  # откатывает и правку кодера
```

Хорошо — `<дерево>` и `<RUN DIR>` подставить литеральными абсолютными путями (корень рабочего
дерева и каталог прогона), каждая строка — отдельный вызов Bash:

```bash
mkdir -p <RUN DIR>/tmp/<роль>/tree && cp -a <дерево>/tools <дерево>/plugins <RUN DIR>/tmp/<роль>/tree/
sed -i 's/"\$SHA"/ffc350e/' <RUN DIR>/tmp/<роль>/tree/tools/run_overlap.sh   # порча — только в копии
python3 -m unittest discover -s <RUN DIR>/tmp/<роль>/tree/tools/tests -p test_run_overlap.py   # ждём FAILED
```

Прежний образец (`T=…; mkdir -p "$T"`, затем `git ls-files > files.txt` и
`xargs -0 cp --parents -t "$T"`) в сессии git worktree не исполним: охранник отвергает
переменную в пути и git внутри конвейера (22.10.2026, feature-trial-fixes, reports/risk-6.md —
форма выше прошла, тест на копии `FAILED (failures=5)`, дерево чистое).

Почему: 22.10.2026, feature-trial-simple — рецензент задачи 1 подложил порчу прямо в
`tools/run_overlap.sh` и вернул файл `git checkout -- tools/run_overlap.sh`: откатилась
незакоммиченная правка кодера. Файл вернули побайтно лишь потому, что рецензент снял его копию
до порчи; у кодера своей копии файла не было, только дифф для REVIEW. Порча — на копии во временном
каталоге, либо после коммита задачи с возвратом из этого коммита. Никогда
`git checkout`/`git restore`/`git stash` по файлам, у которых есть незакоммиченная чужая правка.
`cp -a` берёт каталоги с диска целиком — с незакоммиченными правками, но и с неотслеживаемым
(`__pycache__`, кэши): для проверки теста это безвредно, а как снимок того, что уйдёт в коммит,
такая копия не годится.
