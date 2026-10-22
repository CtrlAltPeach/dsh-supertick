# Примеры для .conventions/

Образцы на выдуманном проекте «FastAPI + React» (сервис записи встреч): как выглядит хороший эталон,
антипример и проверка. Стек здесь для наглядности — в своём проекте бери его язык и его узоры.

## Образец

`gold-standards/api-endpoint.py` — эндпоинт, отдающий запись владельцу:

```python
# Эндпоинт чтения: схема ответа, зависимость текущего пользователя, проверка владельца.
# Смотреть: 404 и на «нет записи», и на «чужая запись» — существование не выдаём.
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import CurrentUser, get_current_user
from app.db import Database, get_db

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


class MeetingOut(BaseModel):
    id: str
    title: str
    duration_sec: int


@router.get("/{meeting_id}", response_model=MeetingOut)
async def get_meeting(
    meeting_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> MeetingOut:
    doc = await db.meetings.find_one({"_id": meeting_id, "owner_id": user.id})
    if doc is None:
        raise HTTPException(status_code=404, detail="meeting_not_found")
    return MeetingOut(id=doc["_id"], title=doc["title"], duration_sec=doc["duration_sec"])
```

## Антипример

`anti-patterns/avoid-hardcoded-colors.md`:

Цвет в компоненте пишут токеном палитры, а не числом. Захардкоженный цвет не меняется вместе с
темой, и при смене акцента его приходится искать по всему коду.

Плохо:

```tsx
<button style={{ background: "#4f46e5", color: "#fff" }}>Начать встречу</button>
```

Хорошо:

```tsx
<Button variant="primary">Начать встречу</Button>
// внутри Button: background: var(--color-accent); color: var(--color-on-accent);
```

Почему: токен один на весь проект — тёмная тема и новая палитра правятся в одном файле, а
сторож палитры в CI ловит любой `#rrggbb` вне неё.

## Проверка

`checks/naming.md`:

| Что | Правило | Регулярка | Да | Нет |
|---|---|---|---|---|
| файл компонента | PascalCase, `.tsx` | `^[A-Z][A-Za-z0-9]+\.tsx$` | `MeetingCard.tsx` | `meeting-card.tsx` |
| хук | `use` + PascalCase | `^use[A-Z][A-Za-z0-9]+\.ts$` | `useMeetings.ts` | `meetingsHook.ts` |
| модуль Python | snake_case | `^[a-z][a-z0-9_]*\.py$` | `mail_events.py` | `MailEvents.py` |
| булево поле | префикс `is_` / `has_` | `^(is\|has)_[a-z_]+$` | `is_paid` | `paid_flag` |
| маршрут API | kebab-case, мн. число | `^/api/[a-z-]+s(/\|$)` | `/api/meetings` | `/api/Meeting` |

Нарушение регулярки — замечание рецензента; исключения перечисляются тут же, списком с причиной.
