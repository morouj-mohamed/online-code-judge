# tools/build_presentation_pdf.py - turn docs/PRESENTATION.md into docs/PRESENTATION.pdf (needs: pip install markdown, the fonts-noto-core package, and Playwright to print).
import markdown, re, html, os
SRC = "/home/user/online-code-judge/docs/PRESENTATION.md"
SHOTS = "/home/user/online-code-judge/docs/screenshots"
md = open(SRC, encoding="utf-8").read()
# drop the first H1 + intro quote: the cover page replaces them
md = re.sub(r"\A# [^\n]*\n+(> [^\n]*\n)+\s*---\s*\n", "", md)
body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
# code is always left-to-right
body = body.replace("<pre>", '<pre dir="ltr">').replace("<code>", '<code dir="ltr">')
# Arabic words inside code blocks: give them the Arabic font (the monospace
# font has only isolated Arabic letters, so words would look broken apart).
AR = r"[\u0600-\u06FF][\u0600-\u06FF\u064B-\u065F ]*[\u0600-\u06FF]|[\u0600-\u06FF]"
body = re.sub(r"(<pre[^>]*>)(.*?)(</pre>)", lambda m: m.group(1) + re.sub(AR, lambda a: f'<span class="ar">{a.group(0)}</span>', m.group(2)) + m.group(3), body, flags=re.S)
shots = [
    ("home.png", "الصفحة الرئيسية: المسائل، والبحث والفلاتر، وحالة الـ judge live"),
    ("editor.png", "صفحة المسألة: نص المسألة على الشمال والـ editor على اليمين، و Run sample بيعرض النتيجة تحت الكود"),
    ("result.png", "صفحة النتيجة: الحكم، والوقت والذاكرة اللي قاسهم الـ kernel، والـ tests بتنور واحدة واحدة، والستيكر"),
    ("inspector.png", "الـ OS Inspector: الـ worker threads والـ bounded queue بالـ semaphores أثناء الـ stress test"),
    ("lifecycle.png", "دورة حياة process حقيقي: من fork() لحد wait4()، اتقتل بـ SIGXCPU (24) ← Time Limit Exceeded"),
    ("standings.png", "الـ Standings بنظام ICPC، وعمود الـ Exits في الـ Exam mode، والـ Announcements"),
    ("admin-problem.png", "صفحة المدرس لتعديل مسألة: الحدود، وطريقة المقارنة، والصور، والـ tests، والـ Rejudge"),
]
gallery = "".join(f'<figure class="shot"><img src="file://{SHOTS}/{f}"><figcaption>{html.escape(c)}</figcaption></figure>' for f, c in shots)
page = f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>Nova Coding Space — شرح المشروع</title>
<style>
@page {{ size: A4; margin: 16mm 14mm 18mm 14mm; }}
:root {{ --accent: #AD1457; --ink: #1D1520; --muted: #5A4D5E; --line: #E9DEE4; --soft: #FBF4F7; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Noto Sans Arabic", "Noto Naskh Arabic", "DejaVu Sans", sans-serif; color: var(--ink); font-size: 10.6pt; line-height: 1.75; margin: 0; }}
.cover {{ height: 255mm; display: flex; flex-direction: column; justify-content: center; align-items: flex-start; padding: 0 6mm;
  background: linear-gradient(160deg, #1a0b14 0%, #2a1020 55%, #6d0f3a 100%); color: #fff; border-radius: 6mm; page-break-after: always; }}
.cover .logo {{ width: 22mm; height: 22mm; border-radius: 5mm; background: #C2185B; display: grid; place-items: center; font: 700 26pt "DejaVu Sans Mono", monospace; margin-bottom: 10mm; }}
.cover h1 {{ font-size: 34pt; margin: 0 0 3mm; line-height: 1.2; font-family: "Noto Sans Arabic", sans-serif; direction: ltr; }}
.cover h2 {{ font-size: 17pt; margin: 0 0 10mm; color: #F8A0C4; border: 0; font-weight: 600; }}
.cover p {{ font-size: 12pt; max-width: 150mm; color: #f2e6ee; margin: 0 0 3mm; }}
.cover .tags {{ margin-top: 10mm; display: flex; flex-wrap: wrap; gap: 2mm; direction: ltr; }}
.cover .tags span {{ font: 9.5pt "DejaVu Sans Mono", monospace; border: 1px solid rgba(255,255,255,.35); border-radius: 99px; padding: 1mm 3mm; }}
.cover .foot {{ margin-top: auto; margin-bottom: 12mm; font-size: 10pt; color: #e6cfdb; }}
h2 {{ color: var(--accent); font-size: 16pt; margin: 9mm 0 3mm; padding-bottom: 1.5mm; border-bottom: 2px solid var(--accent); page-break-after: avoid; }}
h3 {{ font-size: 12.5pt; margin: 6mm 0 2mm; color: #3a1630; page-break-after: avoid; }}
h2:first-of-type {{ margin-top: 0; }}
p {{ margin: 0 0 2.5mm; }}
strong {{ color: #2a0f20; }}
code {{ font-family: "DejaVu Sans Mono", "Noto Sans Arabic", monospace; font-size: 8.8pt; background: var(--soft); border: 1px solid var(--line); border-radius: 3px; padding: 0 1.2mm; unicode-bidi: isolate; white-space: nowrap; }}
pre {{ font-family: "DejaVu Sans Mono", "Noto Sans Arabic", monospace; background: #1a0f17; color: #f5e6ee; border-radius: 3mm; padding: 4mm 5mm; font-size: 8.3pt; line-height: 1.5; overflow: hidden; white-space: pre-wrap; page-break-inside: avoid; text-align: left; }}
pre .ar {{ font-family: "Noto Sans Arabic", sans-serif; font-size: 8.6pt; unicode-bidi: isolate; direction: rtl; }}
pre code {{ font-family: inherit; background: none; border: 0; padding: 0; color: inherit; white-space: pre-wrap; font-size: inherit; }}
table {{ width: 100%; border-collapse: collapse; margin: 2mm 0 4mm; font-size: 9.4pt; page-break-inside: auto; }}
tr {{ page-break-inside: avoid; }}
th {{ background: var(--accent); color: #fff; font-weight: 600; text-align: right; padding: 1.8mm 2.5mm; }}
td {{ border-bottom: 1px solid var(--line); padding: 1.8mm 2.5mm; vertical-align: top; }}
tr:nth-child(even) td {{ background: #fdf8fa; }}
td code {{ white-space: normal; }}
ul, ol {{ margin: 0 0 3mm; padding-right: 6mm; padding-left: 0; }}
li {{ margin-bottom: 1mm; }}
hr {{ border: 0; border-top: 1px dashed var(--line); margin: 6mm 0; }}
blockquote {{ margin: 3mm 0; padding: 2.5mm 4mm; background: var(--soft); border-right: 3px solid var(--accent); border-radius: 2mm; }}
.gallery h2 {{ page-break-before: always; }}
.shot {{ margin: 0 0 7mm; page-break-inside: avoid; text-align: center; }}
.shot img {{ max-width: 100%; max-height: 105mm; border: 1px solid var(--line); border-radius: 2mm; }}
.shot figcaption {{ font-size: 9.6pt; color: var(--muted); margin-top: 1.5mm; }}
</style></head><body>
<section class="cover">
  <div class="logo">&lt;/&gt;</div>
  <h1>Nova Coding Space</h1>
  <h2>Online Judge لمادة نظم التشغيل — شرح المشروع للعرض</h2>
  <p>كل كود بيتبعت بيتشغّل في process معزول بأدوات نظام التشغيل نفسها، والـ OS Inspector بيعرض ده وهو بيحصل.</p>
  <div class="tags"><span>fork()</span><span>execve()</span><span>wait4()</span><span>setrlimit()</span><span>chroot()</span><span>setresuid()</span><span>killpg()</span><span>semaphores</span><span>threads</span><span>pipes</span><span>signals</span></div>
  <div class="foot">Operating Systems course project · 2026</div>
</section>
<main>{body}</main>
<section class="gallery"><h2>ملحق: صور الصفحات</h2>{gallery}</section>
</body></html>"""
open("/tmp/claude-0/pdf/presentation.html", "w", encoding="utf-8").write(page)
print("html ok", len(page))
