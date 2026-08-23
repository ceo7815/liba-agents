# liba-agents

מערכת סוכנים לסוכנות הביטוח Liba. הראנרים מדווחים ל-Liba OS דרך `/api/mcp`. אין להם מסך משלהם.

```
profile: call-qa         ← בקרת שיחות
profile: social-media    ← פרסום פייסבוק/אינסטגרם

Liba OS  ← יומן, אישורים, עלויות, תגובות. הסוכנים לא מדברים עם Supabase.
```

לא לפרוס על `beo-systems-1` — שרת Beo OS אינו לנתוני ליבה.

## Deploy on xCloud (24/7)

1. עצרו worker מקומי.
2. ב-xCloud על שרת **Liba Insurance** (לא beo-systems-1): **+ New Site** → **Custom Docker** → **Docker Compose From Git**.
3. GitHub `ceo7815/liba-agents`, branch `main`, compose `docker-compose.yml`.
4. Port **8080**.
5. Environment File לפי `.env.example`. עד חיבור Meta: `SOCIAL_PUBLISH_ENABLED=0` (רק heartbeat).
6. Auto-deploy on push + HTTPS.
7. `LIBA_OS_BASE_URL` = כתובת ציבורית של Liba OS, לא localhost.

כשמוכנים לפרסום: טוקני Meta, `SOCIAL_DRY_RUN=0`, `SOCIAL_PUBLISH_ENABLED=1`.

## מבנה

```
agents/call-qa/        בקרת איכות שיחות
agents/social-media/   פרסום מאושר
shared/                os_client, STT, Meta Graph
deploy/                xCloud health + start
```
