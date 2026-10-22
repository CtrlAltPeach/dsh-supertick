# Реестр протокола отстал от роли

Формат сообщения поменяли в роли, а в реестре (`spec/protocol.md` прогона) и в других ролях —
нет. 21.10.2026 (L14, L19): `coder.md` уже писал REVIEW со строкой `Diff:` и DONE со строкой
`COMMITS:`, а реестр — REVIEW с `Commits:` и DONE в четыре строки; `proxy-teammate.md` слал
`SECOND OPINION` без `Findings:`. Стыки чинили задачи 18, 23 и 13.

Плохо — правка формата в одном файле:

```text
coder.md:   DONE: task #<id> / COMMITS: <sha …> / SUMMARY: …
protocol:   DONE: task #<id> + SUMMARY:, REVIEW:, EDGE CASES: (4 строки)
```

Хорошо — формат меняется у отправителя, у получателя и в реестре одним заходом, затем сверка:

```bash
python3 tools/token_check.py --spec-dir <каталог spec>    # ИТОГ: … расхождений=0
grep -rn 'SECOND OPINION' plugins/supertick              # все места одного токена
```

Почему: `token_check.py` сверяет только головы токенов; хвостовые строки (`Diff:`, `COMMITS:`,
`Findings:`) проверяет grep по всем местам токена.
