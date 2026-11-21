# قائمة اختبار كاملة — Nova Coding Space

كل بند له رقم (مثلاً **4.3**) عشان لو حاجة باظت تقولي رقمها. علّم `[x]` بعد ما تجرّبه.
الترتيب مقصود: كل جزء بيبني على اللي قبله.

**قبل ما تبدأ**
```bat
cd /d "D:\judge project\online-code-judge"
git pull
docker compose up --build
```
الموقع: `http://127.0.0.1:5000` (أو `https://127.0.0.1:5000` — أول مرة: Advanced → Continue).

أول أدمن (مرة واحدة بس، في CMD تاني):
```bat
docker compose exec judge python3 tools/manage_users.py create-admin teacher
```

---

## 0) الاختبارات الأوتوماتيك (في CMD تاني)
- [ ] **0.1** `docker compose exec judge python3 tools/api_smoke_test.py` → آخر سطر `103/103 checks passed`
- [ ] **0.2** `docker compose exec judge python3 tools/run_verdict_tests.py` → `14/14 passed (isolation: full)`
- [ ] **0.3** `docker compose exec judge python3 tools/test_concurrency.py` → `40/40 checks passed`
- [ ] **0.4** `docker compose exec judge python3 tools/stress.py --plain` → كل الـ 20 submission أخدوا الـ verdict المتوقع

> لو شغّلت الـ smoke test مرتين ورا بعض بسرعة، أجزاء الـ rate limit هتفشل (طبيعي) — استنى دقيقة.

## 1) زائر من غير حساب
- [ ] **1.1** الصفحة الرئيسية: الاسم **Nova Coding Space** فوق وفي عنوان التاب، والـ chip `Judge online 0/3 workers busy queue 0` بنقطة **خضرا**
- [ ] **1.2** البحث عن `prime` → مسألة واحدة · فلتر `Hard` → مسألة واحدة · كلمة مش موجودة → "No problem matches" + زرار Clear
- [ ] **1.3** الضغط على tag (مثلاً `math`) يفلتر المسائل
- [ ] **1.4** `Ctrl+K` → اكتب `fib` → Enter → يفتح المسألة
- [ ] **1.5** زرار الشمس/القمر يغيّر الثيم، ويفضل محفوظ بعد refresh
- [ ] **1.6** صفحة مسألة: الـ statement، زرار Copy للـ sample، سحب الفاصل في النص يغيّر العرض
- [ ] **1.7** تغيير اللغة يحط template مناسب · `+` / `−` يغيّروا حجم الخط · Reset يسألك الأول وبعدين يرجّع الـ template
- [ ] **1.8** تكتب كود وتعمل refresh → الكود لسه موجود
- [ ] **1.9** الفوتر: "Operating Systems course project · 2026" ولينك **Source code** بيفتح GitHub
- [ ] **1.10** رابط غلط (مثلاً `/problems/xyz`) → صفحة "This page does not exist" مرتبة + زرار Browse problems

## 2) كل الـ verdicts (على مسألة A)
| # | اكتب الكود ده | المتوقع |
|---|---|---|
| **2.1** | Python: `a,b=map(int,input().split())` + `print(a+b)` | **Accepted** + confetti + ستيكر |
| **2.2** | نفس الكود بـ `a-b` | **Wrong Answer** + كارت "Sample check" فيه diff |
| **2.3** | C++: `int a,b;` بدل `long long` | **Wrong Answer** على test 5 (overflow) والـ sample بيعدّي |
| **2.4** | C: `int main(){return x;}` | **Compilation Error** + رسالة الـ compiler |
| **2.5** | Python: `while True: pass` | **Time Limit Exceeded** |
| **2.6** | Python: `a=[0]*10**9` | **Memory Limit Exceeded** |
| **2.7** | C: `int main(){int *p=0; *p=1;}` | **Runtime Error** (SIGSEGV) |
| **2.8** | كود Python واللغة C++ | تحذير "This looks like Python" + زرار Switch |

- [ ] **2.9** `Ctrl+Enter` = Run sample (النتيجة تحت الكود) · `Ctrl+Shift+Enter` = Submit
- [ ] **2.10** صفحة النتيجة: الخانات بتنور واحدة واحدة، والجدول بيتملي live
- [ ] **2.11** "Under the hood" بيعرض fork / setsid / chroot / exec / reaped
- [ ] **2.12** "Source code" بيعرض الكود اللي اتبعت · زرار "Edit & resubmit" يرجّعك للمسألة
- [ ] **2.13** صفحة Submissions: الحالة بتتغيّر قدامك من غير refresh
- [ ] **2.14** الصفحة الرئيسية بعد الحل: المسألة عليها **Solved**

## 3) الحسابات
- [ ] **3.1** Sign up بإيميل → رسالة "not an email" · باسورد أقل من 8 → رسالة واضحة
- [ ] **3.2** Sign up صح → رسالة "N submissions were added to your account" (اللي عملتهم وانت زائر)
- [ ] **3.3** قائمة الاسم فوق → My submissions / Log out
- [ ] **3.4** Log out → الـ submissions بتاعة الحساب مش ظاهرة · Log in تاني → ترجع
- [ ] **3.5** Log in بباسورد غلط → "wrong username or password" · 10 محاولات غلط ورا بعض → "Too many tries… Please wait about 5 minutes"
- [ ] **3.6** افتح نفس الحساب من متصفح تاني → نفس الـ submissions

## 4) المدرّس — المسائل
- [ ] **4.1** Admin → New problem → العنوان يملأ الـ slug والحرف لوحده · الـ preview بيتحدّث وانت بتكتب
- [ ] **4.2** Create → المسألة **Hidden** (الطالب مش شايفها)
- [ ] **4.3** ارفع ZIP فيه `1.in 1.out 2.in 2.out` → "2 tests uploaded"
- [ ] **4.4** ارفع ZIP ناقص `.out` → رسالة رفض واضحة
- [ ] **4.5** Run on all tests بحل صح → Accepted · بحل غلط → يوريك الـ expected كامل (للمدرّس بس)
- [ ] **4.6** Publish → الطالب يشوفها ويحلّها
- [ ] **4.7** Edit → Save · Delete (لازم تكتب الـ slug)
- [ ] **4.8** Submissions → **All users** يعرض كل الطلبة

## 5) المدرّس — الامتحانات
- [ ] **5.1** New contest: بداية بعد دقيقة، مسألتين، **Exam mode** + Lock after 3 → Create → Publish
- [ ] **5.2** الطالب يشوف العدّاد **STARTS IN** والمسائل مخفية لحد البداية، وبعدها الصفحة تتحدّث لوحدها (**ENDS IN** + المسائل)
- [ ] **5.3** الطالب من غير حساب يحاول Submit → "Log in to take part"
- [ ] **5.4** مسألة جوه الامتحان → شاشة "Exam mode" → Start in fullscreen
- [ ] **5.5** `Esc` أو `Alt+Tab` أو تاب تاني → "You left the exam · 2 exits left" → "1 exit left" → **Your exam is locked**
- [ ] **5.6** وهو مقفول: Submit مرفوض
- [ ] **5.7** لصق كود كبير (أكتر من 200 حرف) → بيتسجّل
- [ ] **5.8** المدرّس → Standings: عمود **Exits** + زرار **Unlock** → الطالب يتفتح لوحده خلال ثواني
- [ ] **5.9** طالبين يحلّوا: اللي حلّ من غير غلط يطلع الأول · خانة `+1 12'` و `-2`
- [ ] **5.10** **Export CSV** يتفتح في Excel صح
- [ ] **5.11** غيّر نهاية الامتحان لوقت فات → Submit مرفوض ("contest is over")
- [ ] **5.12** في الامتحان مفيش ستيكرز (الحكم بيظهر عادي من غير reaction)

## 6) المدرّس — الطلبة
- [ ] **6.1** Admin → **Add students** → الصق أسماء (سطر لكل اسم) أو ارفع CSV → Create → جدول فيه الباسوردات + Print / Download CSV
- [ ] **6.2** اسم مكرر أو إيميل في القايمة → يتعلّم عليه ومايتعملش
- [ ] **6.3** الطالب يدخل بالباسورد اللي اتطبع
- [ ] **6.4** Admin → اسم طالب → **Reset password** → باسورد جديد · القديم مابقاش شغال
- [ ] **6.5** صفحة الطالب: إحصائيات، رسم الـ verdicts، كل مسألة، كل مرة خرج فيها من امتحان
- [ ] **6.6** طالبين يبعتوا نفس الحل بأسماء متغيرات وتعليقات مختلفة → **Similarity check** → **100%** → Compare يعرض الكودين والسطور المتشابهة ملوّنة

## 7) OS Inspector
- [ ] **7.1** Isolation: `chroot + uid drop + rlimits + process group` والـ 5 بنود عليها ✓
- [ ] **7.2** ابعت حل → Worker يبقى **Busy** بالـ submission والـ PID، وبعدين **Idle** وعدد الـ jobs يزيد
- [ ] **7.3** **Stress test** → الـ queue تتملي وتفضى، الـ 3 workers Busy، produced = consumed، العداد يزيد 20
- [ ] **7.4** C `while(1){}` → الـ timeline: **killed by SIGXCPU (24)** + TLE
- [ ] **7.5** Python `import time; time.sleep(100)` → **watchdog** ثم **SIGKILL (9)** + TLE
- [ ] **7.6** C segfault → **SIGSEGV (11)** · Python `raise SystemExit(3)` → **exited with code 3**
- [ ] **7.7** زرار **why?** يفتح ويقفل الشرح
- [ ] **7.8** Resource limits: القيم = الـ setrlimit في الـ timeline (Python: 3 s, 512 MB)
- [ ] **7.9** Event log: الفلاتر بتخبّي، Pause يوقف ("paused · N new")، Resume يكمل
- [ ] **7.10** **OS concept** → drawer فيه الملف ورقم السطر، واللينك يفتح GitHub على نفس السطر
- [ ] **7.11** من حساب طالب: رسالة "your own submissions" ومش بيشوف processes غيره

## 8) الستيكرز (Reactions)
- [ ] **8.1** كل verdict له ستيكر بيظهر في الركن لما الحكم يوصل، ويقفل لوحده بعد 7 ثواني
- [ ] **8.2** الماوس على الكارت → مايقفلش · `Esc` أو X → يقفل
- [ ] **8.3** فتح submission قديمة → مفيش ستيكر
- [ ] **8.4** "Stop showing these" → مايظهرش تاني · `Ctrl+K` → "Turn verdict reactions on" → يرجع
- [ ] **8.5** Admin → **Reactions** → ارفع GIF/صورة لـ Accepted بتعليق عربي → Preview → الحل الجاي يظهر هو
- [ ] **8.6** ارفع فيديو MP4 → يشتغل صامت + زرار "Sound on"
- [ ] **8.7** ارفع ملف SVG أو HTML غيّرت اسمه لـ `.png` → مرفوض
- [ ] **8.8** شيل علامة "Show reactions after a verdict" → مفيش ستيكرز لأي حد

## 9) شكل وموبايل وإتاحة
- [ ] **9.1** الألوان: بينك توتي في الـ dark والـ light، والكلام كله مقروء
- [ ] **9.2** F12 → أيقونة الموبايل → عرض 320 و 375: الصفحات مترتبة تحت بعض، ومفيش scroll بالعرض، والـ header سطر واحد، وزرار Run/Submit ثابت تحت وانت في الـ editor
- [ ] **9.3** `Tab` بس من غير ماوس: كل الأزرار عليها إطار واضح وتشتغل بـ Enter
- [ ] **9.4** Windows → Settings → Accessibility → Visual effects → Animation effects **Off** → الأنيميشن يختفي (والستيكر يظهر ثابت)

## 10) أمان
- [ ] **10.1** Python: `print(open('/etc/passwd').read())` → Runtime Error (الملف مش موجود جوه الـ chroot)
- [ ] **10.2** برنامج بيطبع الـ input الخفي ويعمل crash → الرسالة مفيهاش أي بيانات من الـ test
- [ ] **10.3** fork bomb: `import os` + `while True: os.fork()` → الموقع يفضل شغال والنتيجة TLE/RE
- [ ] **10.4** طالب يفتح `/admin` → "Not found" · و `/api/admin/problems` → 403
- [ ] **10.5** طالب يفتح `/submissions/<رقم submission طالب تاني>` → مش لاقيها
- [ ] **10.6** زائر يفتح `/api/admin/problems` → 401
- [ ] **10.7** F12 → Network → أي صفحة → Headers فيها `Content-Security-Policy` و `X-Content-Type-Options: nosniff`

## 11) الإيقاف والتشغيل
- [ ] **11.1** ابعت كذا submission فيها `while True: pass` واقفل السيرفر بـ `Ctrl+C` وهي شغالة → "Worker pool stopped cleanly: True"
- [ ] **11.2** `docker compose up` تاني → السطر `re-queued after restart` واللي كان في الطابور يكمل
- [ ] **11.3** الحسابات والـ submissions والستيكرز المرفوعة لسه موجودة (محفوظة في Docker volume)
- [ ] **11.4** `https://127.0.0.1:5000` و `http://127.0.0.1:5000` الاتنين شغالين على نفس البورت
