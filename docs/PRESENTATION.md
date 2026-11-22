# شرح المشروع للعرض — Nova Coding Space

> ملف تذاكري منه قبل ما تشرحي للدكتور. الجزء 1 و2 هما الأهم (العلاقة بالـ OS)،
> الجزء 6 فيه **سيناريو العرض خطوة بخطوة**، والجزء 7 فيه **الأسئلة المتوقعة وإجاباتها**.

---

## 0) الفكرة في 3 جمل (افتتاحية العرض)

**Nova Coding Space** موقع "Online Judge" زي Codeforces بس معمول لمادة نظم التشغيل:
الطالب يكتب كود C أو C++ أو Python ويبعته، والسيرفر **يشغّله فعلاً** على tests مخفية ويقوله
Accepted ولا Wrong Answer ولا Time Limit ... إلخ.

المشكلة اللي بيحلها المشروع مشكلة OS بحتة: **إزاي أشغّل كود غريب مش واثقة فيه على السيرفر بتاعي بأمان؟**
البرنامج ممكن يعمل loop لا نهائي، أو ياكل الذاكرة كلها، أو يعمل fork bomb، أو يحاول يقرأ ملفات السيرفر.
الحل: كل تشغيل بيبقى **process منفصل** معزول بأدوات نظام التشغيل نفسها:
`fork()` و `execve()` و `setrlimit()` و `chroot()` و `setuid()` و signals و `wait4()`.

وفيه صفحة اسمها **OS Inspector** بتعرض الحاجات دي وهي بتحصل **live** من الـ judge الحقيقي:
الـ threads، والـ queue بالـ semaphores، ودورة حياة كل process، والـ signal اللي موّته.

---

## 1) ليه ده مشروع OS؟ (أهم جزء)

كل مفهوم اتدرس في المادة له مكان حقيقي في الكود:

| المفهوم في المادة | بيحصل فين في المشروع | الملف / الـ function |
|---|---|---|
| **Process creation** | كل compile وكل test = `fork()` جديد، وبعدين `execve()` يحوّل الـ child لبرنامج الطالب | `judge/sandbox.py` → `_spawn()` و `_child_main()` |
| **Process termination & zombies** | الأب بيستنى بـ `wait4()` وبياخد exit status + **rusage** (وقت الـ CPU والذاكرة). أي child بيموت بيتعمله reap عشان مايفضلش zombie | `_spawn()`، `_reap_group()` |
| **Orphans / init** | الـ judge عامل نفسه **subreaper** (`PR_SET_CHILD_SUBREAPER`)، فأحفاد الـ fork bomb لما أبوهم يموت بيرجعوا للـ judge مش لـ init، وهو بيعملهم reap | `_become_subreaper()` |
| **Process groups / sessions** | `setsid()` بيدي كل تشغيل process group لوحده، عشان `killpg()` يقتل البرنامج **وكل اللي عمله fork** مرة واحدة | `_child_main()`، `_kill_group()` |
| **Signals** | الـ kernel بيبعت `SIGXCPU` لما الوقت يخلص، `SIGSEGV` للـ segfault، `SIGFPE` للقسمة على صفر، `SIGXFSZ` لو كتب ملف كبير. الـ watchdog بيبعت `SIGKILL` (مايتمسكش ولا يتعمله ignore). الأب بيفهم الـ status بـ `WIFEXITED` / `WIFSIGNALED` / `WTERMSIG` | `classify_run()` |
| **Resource limits** | `setrlimit()`: CPU, Address space (الذاكرة), حجم الملف, عدد الملفات المفتوحة, عدد الـ processes, core dump = 0 | `_child_main()` |
| **Scheduling / fairness** | الـ `RLIMIT_CPU` بيتحسب **لكل process لوحده**، فـ fork bomb من 32 process كان بياكل 8.5 ثانية CPU ويجوّع برامج الطلبة التانيين. الـ watchdog دلوقتي بيجمع CPU **كل الـ group** من `/proc/<pid>/stat` ويقتله لما يعدي نصيبه → بقى 1.7 ثانية بس | `_group_cpu_s()` |
| **Memory** | `RLIMIT_AS` بيحدد الـ virtual memory. الذاكرة الحقيقية = RSS. فيه مشكلة ذكية: `ru_maxrss` بيحسب كمان نسخة الـ judge اللي كانت قبل `execve()`، فالـ judge بيقيس الـ "floor" ده ويقرا `VmHWM` من `/proc/<pid>/status` عشان يطلع ذاكرة البرنامج نفسه | `_measured_memory()`، `_read_vm()` |
| **File descriptors** | `dup2()` بيوصّل 0/1/2 (stdin/stdout/stderr) بملفات الـ test، وكل الـ file descriptors التانية اللي ورثها من الأب بتتقفل | `_close_inherited_fds()` |
| **File system / isolation** | `chroot()` بيخلي فولدر صغير هو الـ `/` بالنسبة للبرنامج، فمايشوفش ملفات السيرفر. الـ jail بتاع Python معمول بـ **hard links** (اسم تاني لنفس الـ inode، مايكلفش مساحة) | `Sandbox._create_dirs()`، `_link_tree()` |
| **Users & permissions** | بعد الـ chroot، الـ child بيتنازل عن الـ root نهائياً بـ `setgroups` و `setresgid` و `setresuid` لـ uid عادي (20000 + رقم الـ worker) | `_child_main()` |
| **IPC – pipes** | الـ child بيكلم الأب عن طريق **pipe عليه close-on-exec**: بيكتب سطر لكل خطوة، ولما `execve()` ينجح الـ kernel بيقفل الـ pipe لوحده، فالأب يقرا EOF ويعرف إن البرنامج بدأ فعلاً | `say()` جوه `_child_main()` |
| **IPC – producer/consumer** | الـ web server بيحط الـ submissions في **bounded buffer** والـ workers بياخدوا منه | `judge/queue.py` |
| **Threads** | 3 worker threads بيحكموا في نفس الوقت. كل thread بيستنى في `wait4()`، ودي syscall بتسيب الـ GIL بتاع Python، فالتوازي حقيقي | `judge/worker.py` |
| **Synchronization** | الـ queue = **2 counting semaphores + mutex** (`empty_slots`, `full_slots`): الـ semaphore الأول وبعدين الـ mutex، ومفيش thread بيمسك lockين مع بعض، فمفيش deadlock. وفي الداتابيز compare-and-swap (`UPDATE … WHERE status='Pending'`) عشان worker واحد بس ياخد كل submission | `BoundedQueue.put/get`، `db.claim()` |
| **Deadlock / back-pressure** | الـ queue لما تتملي (100) الـ producer بيستنى بدل ما الذاكرة تكبر. جربناها بـ 218 submission: الـ queue وصلت 100/100 بالظبط ومحدش ضاع | `BoundedQueue.put()` |
| **Graceful shutdown** | `Ctrl+C` أو `docker stop` (SIGTERM) → السيرفر يستنى الـ submissions اللي شغالة تخلص ويقفل في ثانيتين. ولو اتقفل فجأة، أول ما يقوم بيرجّع اللي كانت شغالة للـ queue ("re-queued after restart") | `app.py`، `db.recover_interrupted()` |
| **Kernel capabilities** | الـ Docker container شايل **كل** الـ capabilities ورجّع 7 بس محتاجهم (`SYS_CHROOT, SETUID, SETGID, KILL, CHOWN, DAC_OVERRIDE, FOWNER`) + `no-new-privileges` + `pids_limit 512` | `docker-compose.yml` |

**جملة تقوليها:** "كل الحاجات دي مش simulation؛ الـ judge بيعمل الـ system calls دي بنفسه بـ `os.fork()` و `os.execve()` و `resource.setrlimit()`... ومفيش `subprocess` خالص لكود الطالب."

---

## 2) رحلة submission واحدة (اشرحيها على السبورة أو على الـ Inspector)

```
المتصفح ──POST /api/submit──▶ Flask (app.py)
                                  │  يحفظ صف Pending في SQLite
                                  ▼
                     BoundedQueue.put(id)      ← producer
          empty_slots.acquire() → mutex → append → full_slots.release()
                                  │
                                  ▼
                     Worker thread: queue.get()  ← consumer
                     UPDATE … WHERE status='Pending'  (worker واحد بس يكسب)
                                  │
             (C/C++) compile في process معزول بحدود
                                  │
                 لكل test:  fork()
                              │ child:
                              ├─ setsid()                 ← process group جديد
                              ├─ dup2() test.in → 0, out → 1, err → 2، وقفل الباقي
                              ├─ setrlimit(CPU, FSIZE, NOFILE, NPROC, CORE)
                              ├─ chroot(jail)             ← مايشوفش ملفات السيرفر
                              ├─ setresuid(20000+n)       ← مش root
                              ├─ setrlimit(RLIMIT_AS)     ← الذاكرة آخر حاجة
                              └─ execve(program)          ← الـ pipe يتقفل = نجح
                              │ parent:
                              ├─ watchdog: wall clock + CPU الـ group كله
                              ├─ wait4() → exit status + rusage
                              └─ reap أي orphans
                                  │
                  الحكم + المقارنة بالـ expected output
                  (يوقف عند أول test غلط)
                                  │
               يتخزن في SQLite + EventBroker ──SSE──▶ المتصفح live
```

**ترتيب الخطوات مقصود:**
- `setsid` الأول، عشان أي حاجة بعده تبقى في الـ group.
- `RLIMIT_AS` **آخر حاجة**، لأن نسخة Python (الـ judge نفسه) لسه محتاجة ذاكرة تكمّل بيها الخطوات.
- `chroot` قبل `setuid`، لأن الـ chroot محتاج root.

**الحدود (C/C++، و Python بتاخد 3× وقت و 2× ذاكرة):**

| الحد | القيمة | لو اتعدى |
|---|---|---|
| `RLIMIT_CPU` | 1 ثانية | `SIGXCPU` → TLE |
| Wall clock (watchdog) | الوقت × 2 + 0.5 = 2.5 ث | `killpg(SIGKILL)` → TLE (برنامج نايم أو blocked) |
| CPU الـ group كله | 1 ث + 0.5 | `killpg(SIGKILL)` → TLE (fork bomb) |
| `RLIMIT_AS` | 256 MB | `malloc` يرجع NULL / `MemoryError` → MLE |
| `RLIMIT_FSIZE` | 16 MB | `SIGXFSZ` → RE |
| `RLIMIT_NOFILE` | 64 | `open()` يفشل بـ `EMFILE` |
| `RLIMIT_NPROC` | 32 | `fork()` يفشل بـ `EAGAIN` |
| `RLIMIT_CORE` | 0 | مفيش core dump |

---

## 3) الأحكام بتتحدد إزاي

| الحكم | إزاي الـ judge بيعرفه |
|---|---|
| **Accepted** | كل الـ tests: exit code 0 والـ output مطابق |
| **Wrong Answer** | الـ output مختلف. الرسالة بتقول أول سطر مختلف بس، من غير ما تكشف الـ test المخفي |
| **Time Limit Exceeded** | `SIGXCPU`، أو الوقت المقاس أكبر من الحد، أو الـ watchdog قتله (wall clock أو CPU الـ group) |
| **Memory Limit Exceeded** | البرنامج خلص الـ address space، أو أعلى ذاكرة استخدمها عدّت الحد |
| **Runtime Error** | اتقتل بـ signal (`SIGSEGV`, `SIGFPE`, `SIGABRT`...) أو exit code مش صفر |
| **Compilation Error** | الـ compiler فشل، وبيتعرض الـ output بتاعه |

**المقارنة لكل مسألة** (المدرس بيختار): exact (زي Codeforces) أو كلمة كلمة أو **أرقام عشرية بنسبة خطأ** (مثلاً `1e-6`، يعني `1.5` و `1.500000` الاتنين صح).

---

## 4) التكنولوجيا وليه اخترناها

| الحاجة | ليه |
|---|---|
| **Python 3** للـ judge | فيه `os.fork/execve/wait4` و `resource.setrlimit` و `os.chroot` مباشرة، فالـ syscalls باينة في الكود |
| **Flask** | web server بسيط؛ كل request في thread |
| **SQLite (WAL mode)** | ملف واحد، والـ WAL بيخلي القراية ماتستناش الكتابة |
| **Server-Sent Events** بدل WebSockets | الاتجاه واحد (السيرفر → المتصفح)، وبيعيد الاتصال لوحده، وبيكمّل من آخر event (`Last-Event-ID`) |
| **Vanilla JS + CodeMirror 6** | من غير build step ولا framework؛ ES modules على طول |
| **Docker** | نفس البيئة على أي جهاز، ومعاها طبقة حماية تانية (capabilities, pids_limit) |

---

## 5) الصفحات واحدة واحدة

### للطالب
1. **Problems (الرئيسية):** قايمة المسائل، فيها بحث وفلتر بالصعوبة والـ tags. عليها chip "Judge online 0/3 workers busy queue 0" جاي live من السيرفر، والنقطة الخضرا معناها شغال. كل مسألة جنبها Solved أو Attempted. `Ctrl+K` بيفتح command palette.
2. **صفحة المسألة:** على الشمال نص المسألة (Markdown وممكن صور) والحدود. لو المسألة بالأرقام العشرية مكتوب "Numbers are accepted within 1e-6". على اليمين editor فيه templates للغات التلاتة وبيحفظ draft لوحده.
   - `Ctrl+Enter` = **Run sample**: يشغّل على الـ sample العام بس ويعرض diff.
   - `Ctrl+Shift+Enter` = **Submit**.
   - لو كتبتي Python واخترتي C++ هيحذرك "This looks like Python".
3. **صفحة النتيجة:** الحكم كبير، الوقت والذاكرة اللي **الـ kernel قاسهم** (wait4)، مربعات الـ tests بتنور واحدة واحدة live. وفيها:
   - جدول لكل test،
   - "Sample check" فيه diff لو WA،
   - "Under the hood": دورة حياة الـ process بالـ PID الحقيقي،
   - الكود اللي اتبعت،
   - **ستيكر كوميك** للحكم (تقدري تقفليه).
4. **Submissions:** كل اللي بعتيه، والحالة بتتغير قدامك من غير refresh.
5. **Contests:** المسابقات. وجوه كل مسابقة:
   - عداد "Starts in" أو "Ends in"،
   - المسائل (مبتظهرش قبل البداية)،
   - **Standings** بنظام ICPC (عدد المحلول، وبعدين الـ penalty = دقيقة الحل + 20 لكل محاولة غلط)،
   - **Announcements** من المدرس توصل لكل الطلبة خلال 15 ثانية.
6. **Exam mode:** الامتحان لازم fullscreen. أي خروج (تاب تاني، برنامج تاني، Esc) أو لصق كود كبير **بيتسجّل بالوقت**. بعد عدد خروجات معين الامتحان **بيتقفل من السيرفر** (مش من المتصفح، فمحدش يقدر يتحايل عليه)، لحد ما المدرس يدوس Unlock.
7. **Account:** تغيير الباسورد. الأجهزة التانية بتعمل Log out.

### للمدرس (Admin)
8. **Admin → Problems:** المسائل كلها (Hidden أو Published وهل اتراجعت).
9. **تعديل مسألة:**
   - العنوان والـ statement مع preview.
   - الحدود وطريقة المقارنة.
   - **صور**.
   - رفع **tests في ZIP**.
   - **Run on all tests** بحل مرجعي عشان يتأكد إن الـ tests صح.
   - **Publish**.
   - **Rejudge all** لو صلّح الـ tests.
10. **مسابقة جديدة:** الوقت، والمسائل، و **Exam mode** + عدد الخروجات اللي بعدها يتقفل.
11. **Add students:** يلصق أسماء الفصل أو يرفع CSV → حسابات وباسوردات تتطبع أو تتنزل CSV.
12. **صفحة طالب:** إحصائياته، ورسم للأحكام، وكل مسألة، وكل مرة خرج من امتحان. وفيها **Reset password**.
13. **Similarity check (كشف الغش):** بيقارن حلول الطلبة **بعد ما يشيل أسماء المتغيرات والتعليقات** (tokens) بطريقة MOSS (winnowing). اتنين غيّروا الأسماء بس → 100%، والسطور المتشابهة بتتلوّن جنب بعض.
14. **Reactions:** يرفع GIF أو فيديو لكل حكم بدل الستيكرز الجاهزة.

### صفحة الـ OS (أهم صفحة في العرض)
15. **OS Inspector** (`/inspector`)، كله **أرقام حقيقية** من الـ judge:
   1. **Worker pool:** الـ 3 threads، Busy أو Idle، رقم الـ submission، الـ child PID، والـ sandbox uid.
   2. **Submission queue:** خانات الـ bounded buffer، وقيم الـ semaphores (`empty_slots` و `full_slots`)، و produced/consumed.
   3. **Process lifecycle:** كل خطوات الـ process من `fork()` لحد `wait4()`، بالوقت والـ PID والـ signal، وزرار **why?** بيشرح كل خطوة.
   4. **Resource limits:** القيم اللي اتطبقت فعلاً على آخر تشغيل.
   5. **Isolation:** الـ judge بيجرب chroot و setuid أول ما يقوم ويقول المتاح بأمانة.
   6. **Event log:** كل الأحداث، وفيه فلتر و Pause.
   7. **Stress test:** 20 submission مرة واحدة، تشوفي الـ queue تتملي والـ workers يفضّوها.
   - زرار **OS concept** في كل panel: الشرح + الملف ورقم السطر اللي بيعمل كده (بيتقرا من الكود وهو شغال، فدايماً صح).

---

## 6) سيناريو العرض (10–12 دقيقة)

**قبلها:** `docker compose up --build`، وادخلي بحساب **teacher** في متصفح، وحساب **طالب** في متصفح تاني (أو Incognito).

1. **(1 دقيقة) الفكرة:** اقري الجزء 0.
2. **(1 دقيقة) الطالب:** افتحي مسألة A، اكتبي:
   `a,b=map(int,input().split())` و `print(a+b)` ← **Submit** ← الـ tests بتنور واحدة واحدة ← Accepted + الستيكر.
   قولي: "كل مربع ده process اتعمله fork و execve وقاسه الـ kernel".
3. **(3 دقايق) الـ OS Inspector** (من حساب المدرس):
   - شاوري على الـ **Isolation**: "chroot + uid drop + rlimits + process group".
   - دوسي **Stress test** ← الـ queue تتملي ← "ده producer/consumer بـ semaphores، والـ 3 workers threads".
   - من الطالب ابعتي C: `int main(){ while(1){} }` ← في الـ Inspector اختاري الـ run ← اشرحي الخطوات من `fork()` لحد **killed by SIGXCPU (24)** ← دوسي **why?** على خطوة.
   - ابعتي Python: `import time` و `time.sleep(100)` ← "**watchdog** ← SIGKILL (9)، لأن البرنامج نايم مش بياكل CPU، فـ RLIMIT_CPU مش هيوقفه".
   - ابعتي fork bomb (`tests/fork_bomb.c`) ← "**group CPU limit** + **31 orphans reaped**، الـ judge subreaper".
   - دوسي **OS concept** على الـ queue ← بيوريكي `judge/queue.py` والسطر.
4. **(1 دقيقة) Segfault و Runtime Error:** `int main(){int *p=0; *p=1;}` ← **SIGSEGV (11)**.
5. **(2 دقيقة) الامتحان:** اعملي contest بـ Exam mode ← الطالب يخرج من الـ fullscreen ← "2 exits left"… ← **locked** ← المدرس **Unlock** من الـ Standings.
6. **(1 دقيقة) الأمان:** Python: `print(open('/etc/passwd').read())` ← **Runtime Error** لأن الملف مش موجود جوه الـ chroot.
7. **(1 دقيقة) الختام:** صفحة الريبو على GitHub: الـ README، وعلامة **tests passing** (كل push بيتجرب لوحده)، و `TESTING.md` فيه 100 بند.

> **نصيحة:** لو النت أو الـ Docker عمل مشكلة وقت العرض، خلي معاكي screenshots من `docs/screenshots/`.

---

## 7) أسئلة متوقعة من الدكتور وإجاباتها

**س: ليه `fork` + `execve` ومش `subprocess`؟**
ج: عشان أتحكم في كل خطوة **بين** الـ fork والـ exec: الـ setsid والـ rlimits والـ chroot والـ setuid. والمادة نفسها عن الـ system calls دي. الـ `subprocess` بيخبيها.

**س: إيه الفرق بين `RLIMIT_CPU` والـ watchdog؟**
ج: `RLIMIT_CPU` بيحسب وقت المعالج بس. برنامج عامل `sleep` أو مستني input مش بياكل CPU، فعمره ما هيوصل للحد. عشان كده فيه watchdog بيحسب الـ wall clock (الوقت × 2 + 0.5) ويبعت `SIGKILL` للـ group كله.

**س: ليه الـ `SIGKILL` ومش `SIGTERM`؟**
ج: `SIGKILL` ماينفعش البرنامج يمسكه أو يعمله ignore. لو بعتنا `SIGTERM` كان ممكن الطالب يعمل handler ويكمل.

**س: إيه هو الـ zombie وإنتو بتتعاملوا معاه إزاي؟**
ج: process خلص بس أبوه لسه ماعملش `wait` عليه، فلسه واخد مكان في جدول الـ processes. إحنا بنعمل `wait4()` على كل child، والـ judge **subreaper**، فحتى أحفاد الـ fork bomb بيرجعوا له ويتعملهم reap. الاختبارات بتتأكد إن مفيش zombie واحد بيفضل.

**س: الـ fork bomb بيتوقف إزاي؟**
ج: 4 طبقات:
1. `RLIMIT_NPROC = 32` لكل uid (وكل worker له uid لوحده).
2. CPU الـ group كله محدود: الـ watchdog بيجمعه من `/proc`.
3. `killpg()` بيقتل الـ group مرة واحدة.
4. `pids_limit 512` على الـ container كله.

**س: إزاي بتمنعوا deadlock في الـ queue؟**
ج: ترتيب ثابت. الأول الـ semaphore (اللي ممكن يستنى)، وبعدين الـ mutex (لحظة صغيرة)، ومفيش thread بيمسك اتنين locks مع بعض. ولما الـ queue تتملي الـ producer بيستنى (back-pressure) بدل ما يحصل overflow.

**س: الـ threads في Python مش محدودة بالـ GIL؟**
ج: الـ worker معظم وقته واقف في `wait4()`، ودي system call بتسيب الـ GIL. فالتلات برامج بيشتغلوا على 3 cores فعلاً. في الـ stress test الـ 20 submission خلصوا أسرع بـ 2.5× من لو اتحكموا ورا بعض.

**س: إزاي بتقيسوا الذاكرة؟**
ج: `ru_maxrss` من `wait4()`. بس هو بيحسب كمان نسخة الـ judge اللي كانت قبل الـ `execve`، فبنقيس الـ floor ده الأول، وبنقرا `VmHWM` من `/proc/<pid>/status` وهو شغال، وناخد ذاكرة البرنامج الحقيقية.

**س: الـ chroot كفاية للأمان؟**
ج: لوحده لأ، عشان كده هو طبقة من كذا: `chroot` + uid مش root + rlimits + process group + watchdog + Docker من غير capabilities ومعاه `no-new-privileges`. ولو عايزين أقوى: seccomp (فلتر للـ system calls) أو namespaces/cgroups، ودي تحسينات مستقبلية (اذكريها بأمانة).

**س: إيه اللي يحصل لو السيرفر وقع وفيه submissions شغالة؟**
ج: أول ما يقوم، `recover_interrupted()` بيرجّع الـ Running لـ Pending ويحطهم في الـ queue تاني ("re-queued after restart"). ولو اتقفل بـ Ctrl+C أو docker stop، بيستنى اللي شغال يخلص الأول.

**س: الطالب يقدر يشوف الـ tests المخفية؟**
ج: لأ. رسالة WA بتقول رقم السطر بس. وحتى لو البرنامج نسخ الـ input على stderr وعمل crash، الرسالة بتتكتب من جديد (`public_message`) من غير أي حاجة من الـ test.

**س: كشف الغش بيشتغل إزاي؟**
ج: بيحول الكود لـ tokens ويشيل أسماء المتغيرات والتعليقات والمسافات، وبيعمل fingerprints بـ winnowing (k = 8، window = 4) زي MOSS، وبيقارن نسبة الـ fingerprints المشتركة.

**س: ليه SSE ومش polling؟**
ج: SSE اتصال واحد مفتوح، والسيرفر بيبعت كل event أول ما يحصل. ولو النت قطع، المتصفح بيرجع يكمّل من آخر رقم event (`Last-Event-ID`) من غير ما يضيع حاجة.

**س: جربتوه إزاي؟**
ج: 4 مجموعات اختبارات أوتوماتيك:
- 26 حكم (كل نوع برنامج + الحلول المرجعية)،
- 40 للـ concurrency،
- 103 للـ API،
- 20 submission مع بعض في الـ stress.

بيشتغلوا لوحدهم على GitHub مع كل push. وغيرهم checklist يدوية 100 بند في `TESTING.md`.

---

## 8) أرقام تقوليها

- **3** worker threads، و queue سعتها **100**، و **2** semaphores + **1** mutex.
- fork bomb: كان بياكل **8.5 ث** CPU، بقى **1.7 ث**، ويتقفل في **0.6 ث** بدل 2.6.
- rejudge لـ **218** submission في queue سعتها 100: وصلت **100/100** بالظبط، وكل الأحكام طلعت **نفس الأحكام** (الـ judge deterministic).
- الـ stress: 20 submission أسرع بـ **2.5×** من لو اتحكموا ورا بعض.
- Graceful shutdown في **1.8 ث** وهو بيحكم.
- اختبارات: **103 + 26 + 40 + 7** أوتوماتيك، و **100** بند يدوي، وعلى GitHub مع كل push.

---

## 9) لو الدكتور سأل "إيه اللي ممكن يتحسن؟" (قولي بثقة)

- **seccomp** عشان نمنع system calls خطيرة، و **cgroups** عشان نحدد الذاكرة والـ CPU بدقة أعلى من rlimits.
- **namespaces** (PID و network) بدل chroot بس: البرنامج مايشوفش processes تانية ولا يقدر يفتح network.
- لغات زيادة (Java).
- أكتر من سيرفر judge (distributed workers).
