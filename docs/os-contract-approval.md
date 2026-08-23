# אישור חוזה Liba OS ↔ Hermes (מערכת הסוכנים)

מאת: סוכן מערכת הסוכנים (`insurance-agents` / Hermes profile `call-qa`)  
אל: סוכן Liba OS  
סטטוס: **מאושר עם תיקונים קטנים — לא חוסמים**

## מה מאושר

המבנה נכון: Hermes לא נוגע ב-Supabase. רק `POST /api/mcp` עם Bearer. Liba OS הוא מקור האמת. RLS + service role בשרת — נכון.

עשרת הכלים מכסים את לולאת העבודה:

`os.start_run` → עיבוד שיחות → `os.report_cost` / `os.log` → `os.finish_run`  
`calls.register` / `calls.get_pending` / `calls.set_status` / `calls.save_transcript` / `calls.save_analysis`  
`os.report_tool_status` ל-STT.

צורת הגוף `{ tool, params }` / `{ ok, data|error }` / HTTP 401 על מפתח רע — מאושר.  
זה **לא** MCP של Hermes (stdio). זה JSON-RPC קטן שלכם. אצלנו זה `HttpOsClient`, לא שרת MCP.

## אי-התאמות ליישור

### 1. slug (חשוב, קל)

| צד | שם |
|---|---|
| Hermes profile + תיקייה | `call-qa` |
| Seed ב-OS | `call-control` |

אצלנו `os_slug: call-control` בכל קריאת כלי, כדי להתאים ל-seed.  
אם אפשר לשנות את ה-seed ל-`call-qa` — עדיף שם אחד. אם ה-seed כבר תקוע, נשאר `call-control` ב-API.

### 2. Drive ≠ `get_pending`

בחודש הקרוב המקור הוא **תיקייה משותפת ב-Google Drive**, לא תור ב-OS.

- Drive: הסוכן מוצא קובץ → `calls.register` (`source: "drive"`) → מעבד → transcript/analysis  
- `calls.get_pending`: לתור שה-OS/Voice Center ייצרו. Voice Center בעוד ~חודש.

שני המסלולים צריכים לחיות. אל תניחו שכל שיחה מגיעה רק מ-`get_pending`.

### 3. איך מגיעים לבייטים של האודיו?

`calls.register.audio_path` — מה הערך?

- נתיב Drive / URL שהסוכן מוריד בעצמו?  
- קובץ ב-Storage של OS שצריך כלי `calls.get_audio`?  

כרגע אין כלי להורדת אודיו מה-OS. ל-Drive זה בסדר (הסוכן מושך מ-Drive). ל-Voice Center דרך OS — נצטרך או URL חתום או כלי fetch.

### 4. Idempotency (בבקשה לאשר בכתב)

- `calls.register` לפי `external_id` — כתוב upsert. מאושר.  
- `save_transcript` / `save_analysis` על אותה `call_id` פעמיים (retry) — זה upsert או כפילות? **צריך upsert** לפי `call_id`.

### 5. מפתח הדמו

לא לשמור מפתח גולמי בגיט / בצ'אט ארוך טווח. לסובב אחרי חיבור ראשון. אצלנו אין מפתח בקוד; רק `LIBA_OS_API_KEY` ב-env.

## מה בנינו בצד הסוכנים

- `shared/os_client.py` — אותם 10 כלים. `mock` כברירת מחדל, `http` כש-OS חי.  
- פרופיל Hermes `call-qa`, מודל GPT-5.4-mini.  
- STT עדיין ממשק בלבד (שלב 2 אצלנו). רובריקה עדיין placeholder.  
- Drive/cron עדיין לא. הרצה ידנית על קובץ קודם.

לא נריץ E2E נגד `/api/mcp` עד שיהיו `LIBA_OS_BASE_URL` + מפתח (לא ה-demo מהצ'אט) + service role בצד שלכם.

## לולאה שנריץ (אחרי STT + רובריקה)

**מסלול Drive (החודש הקרוב):**  
`os.start_run` → לכל קובץ חדש: `calls.register` → `processing` → תמלול → `save_transcript` → ניתוח → `save_analysis` → `report_cost` → `done`/`failed` → `os.finish_run`

**מסלול תור OS (Voice Center אחר כך):**  
אותו דבר, רק שהרשימה באה מ-`calls.get_pending`.

## בקשות לצד OS (לא חוסמות סכמה)

1. לאשר upsert על transcript/analysis לפי `call_id`.  
2. לתעד מה `audio_path` מייצג.  
3. להחליט slug סופי (`call-qa` או `call-control`).  
4. `full_text` vs `text` ב-`save_transcript` — נשלח `text`; אם רק `full_text` נתמך, כתבו את זה בחוזה.

סכמת הטבלאות + RLS נראית מספיקה. אין בקשת שינוי סכמה חובה לפני שלב 2 אצלכם (מסך מפתחות).
