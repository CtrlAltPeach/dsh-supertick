# Шаг CI, который врёт

Шаги GitHub Actions идут под `bash -e`. Проверено в этом прогоне (R5, L2):
- `[ -d tools/tests ] && …` последней строкой: каталога нет — шаг красный (rc=1);
- `! grep …` не последней строкой шаг не валит никогда (`-e` не срабатывает на `!`);
- `bash -n a.sh b.sh` проверяет только первый файл, остальные — аргументы.

Плохо:

```yaml
run: |
  ! grep -RIn "<запрещённое>" plugins/supertick
  bash -n plugins/supertick/scripts/*.sh
```

Хорошо — одна проверка на шаг, явный `if … fi` и цикл:

```yaml
run: |
  if grep -RIn "<запрещённое>" plugins/supertick; then exit 1; fi
---
run: |
  for f in plugins/supertick/scripts/*.sh; do bash -n "$f"; done
```

Почему: зелёный шаг, который не может покраснеть, хуже отсутствующего. Новый шаг проверить
подложенной порчей: он обязан упасть.
