# قائمة اختبار يدوي كاملة — ForkJudge

علّم `[x]` على كل بند بعد ما تجرّبه. الترتيب مقصود: كل جزء بيبني على اللي قبله.

**قبل ما تبدأ**
```bat
cd /d "D:\judge project\online-code-judge"
git pull
docker compose up --build
```
الموقع: `http://127.0.0.1:5000` (أو `https://...` — أول مرة: Advanced → Continue).

---

## 0) الاختبارات الأوتوماتيك (5 دقايق، في CMD تاني)
- [ ] `docker compose exec judge python3 tools/api_smoke_test.py` → آخر سطر `99/99 checks passed`
- [ ] `docker compose exec judge python3 tools/run_verdict_tests.py` → `14/14 passed (isolation: full)`
- [ ] `docker compose exec judge python3 tools/test_concurrency.py` → `40/40 checks passed`
- [ ] `docker compose exec judge python3 tools/stress.py --plain` → كل الـ 20 submission أخدوا الـ verdict المتوقع

> لو شغّلت الـ smoke test مرتين ورا بعض بسرعة، أجزاء الـ rate limit هتفشل (طبيعي) — استنى دقيقة.

---

## 1) زائر من غير حساب
- [ ] الصفحة الرئيسية: العنوان، والـ chip `Judge online 0/3 workers busy queue 0`
- [ ] البحث عن `prime` → مسألة واحدة · فلتر `Hard` → مسألة واحدة · كلمة مش موجودة → "No problem matches" + زرار Clear
- [ ] `Ctrl+K` → اكتب `fib` → Enter → يفتح المسألة
- [ ] زرار الشمس/القمر يغيّر الثيم، ويفضل محفوظ بعد refresh
- [ ] صفحة مسألة: الـ statement، زرار Copy للـ sample، سحب الفاصل في النص يغيّر العرض
- [ ] تغيير اللغة يحط template مناسب · `+` / `−` يغيّروا حجم الخط
- [ ] تكتب كود وتعمل refresh → الكود لسه موجود ("Draft saved")

## 2) كل الـ verdicts (على مسألة A)
| اكتب الكود ده | المتوقع |
|---|---|
| Python: `a,b=map(int,input().split())` + `print(a+b)` | **Accepted** + confetti |
| نفس الكود بـ `a-b` | **Wrong Answer** + كارت "Sample check" فيه diff |
| C++: `int a,b;` بدل `long long` | **Wrong Answer** على test 5 (overflow) والـ sample بيعدّي |
| C: `int main(){return x;}` | **Compilation Error** + رسالة الـ compiler، والوقت `—` |
| Python: `while True: pass` | **Time Limit Exceeded** |
| Python: `a=[0]*10**9` | **Memory Limit Exceeded** |
| C: `int main(){int *p=0; *p=1;}` | **Runtime Error** (SIGSEGV) |
| كود Python واللغة C++ | تحذير "This looks like Python" + زرار Switch |

- [ ] `Ctrl+Enter` = Run sample · `Ctrl+Shift+Enter` = Submit
- [ ] في صفحة النتيجة: الخانات بتنور واحدة واحدة، والجدول بيتملي live
- [ ] "Under the hood" بيعرض fork / setsid / chroot / exec / reaped
- [ ] "Source code" بيعرض الكود اللي اتبعت
- [ ] صفحة Submissions: الحالة بتتغيّر قدامك من غير refresh

## 3) الحسابات
- [ ] Sign up بإيميل → رسالة "not an email" · باسورد أقل من 8 → رسالة واضحة
- [ ] Sign up صح → رسالة "N submissions were added to your account" (اللي عملتهم وانت زائر)
- [ ] قائمة الاسم فوق → My submissions / Log out
- [ ] Log out → الـ submissions بتاعة الحساب مش ظاهرة · Log in تاني → ترجع
- [ ] Log in بباسورد غلط → "wrong username or password"
- [ ] افتح نفس الحساب من متصفح تاني → نفس الـ submissions

## 4) المدرّس — المسائل
(`docker compose exec judge python3 tools/manage_users.py create-admin teacher`)
- [ ] Admin → New problem → العنوان يملأ الـ slug والحرف لوحده · الـ preview بيتحدّث وانت بتكتب
- [ ] Create → المسألة **Hidden** (الطالب مش شايفها)
- [ ] ارفع ZIP فيه `1.in 1.out 2.in 2.out` → "2 tests uploaded"
- [ ] ارفع ZIP ناقص `.out` → رسالة رفض واضحة
- [ ] Run on all tests بحل صح → Accepted · بحل غلط → يوريك الـ expected كامل (للمدرّس بس)
- [ ] Publish → الطالب يشوفها ويحلّها
- [ ] Edit → Save · Delete (لازم تكتب الـ slug)
- [ ] Submissions → **All users** يعرض كل الطلبة

## 5) المدرّس — الامتحانات
- [ ] New contest: بداية بعد دقيقة، مسألتين، **Exam mode** + Lock after 3 → Create → Publish
- [ ] الطالب يشوف العدّاد "Starts in" والمسائل مخفية لحد البداية، وبعدها الصفحة تتحدّث لوحدها
- [ ] الطالب من غير حساب يحاول Submit → "Log in to take part"
- [ ] مسألة جوه الامتحان → شاشة "Exam mode" → Start in fullscreen
- [ ] `Esc` أو `Alt+Tab` → "You left the exam · 2 exits left" … وبعد التالتة → **Your exam is locked**
- [ ] وهو مقفول: Submit مرفوض
- [ ] لصق كود كبير (أكتر من 200 حرف) → بيتسجّل
- [ ] المدرّس → Standings: عمود **Exits** + زرار **Unlock** → الطالب يتفتح لوحده خلال ثواني
- [ ] طالبين يحلّوا: اللي حلّ من غير غلط يطلع الأول · خانة `+1 12'` و `-2`
- [ ] **Export CSV** يتفتح في Excel صح
- [ ] غيّر نهاية الامتحان لوقت فات → Submit مرفوض ("contest is over")

## 6) المدرّس — صفحة الطالب وكشف الغش
- [ ] Admin → اسم طالب → إحصائيات، رسم الـ verdicts، كل مسألة، كل مرة خرج فيها من امتحان
- [ ] طالبين يبعتوا نفس الحل بأسماء متغيرات وتعليقات مختلفة → Similarity check → **100%** → Compare يعرض الكودين والسطور المتشابهة ملوّنة

## 7) شكل وموبايل وإتاحة
- [ ] F12 → أيقونة الموبايل → عرض 375: الصفحات مترتبة تحت بعض، وزرار Run/Submit تحت ثابت
- [ ] `Tab` بس من غير ماوس: كل الأزرار عليها إطار واضح وتشتغل بـ Enter
- [ ] Windows → Settings → Accessibility → Visual effects → Animation effects **Off** → الأنيميشن يختفي

## 8) أمان (اختياري للعرض)
- [ ] Python: `print(open('/etc/passwd').read())` → Runtime Error (الملف مش موجود جوه الـ chroot)
- [ ] برنامج بيطبع الـ input الخفي ويعمل crash → الرسالة مفيهاش أي بيانات من الـ test
- [ ] fork bomb: `import os` + `while True: os.fork()` → الموقع يفضل شغال والنتيجة TLE/RE

## 9) الإيقاف والتشغيل
- [ ] ابعت submission فيها `while True: pass` واقفل السيرفر بـ `Ctrl+C` وهي شغالة → يقفل بهدوء
- [ ] `docker compose up` تاني → السطر `re-queued after restart` واللي كان في الطابور يكمل
- [ ] الحسابات والـ submissions لسه موجودة (محفوظة في Docker volume)
