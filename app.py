import os, re, base64
from datetime import datetime

import gradio as gr
from rapidfuzz import fuzz
import dateparser
from dateparser.search import search_dates

# =========================
# Knowledge base (rule-based)
# =========================
LIFE_EVENTS = {
    "new_baby": {
        "keywords": [
            "baby","newborn","new baby","birth of child","childbirth",
            "gave birth","delivered a baby","our baby arrived","our child arrived",
            "became a parent","became parents","had a baby","have a baby","having a baby",
            "first baby","first child","my kid was born","we had a baby","we've had a baby",
            "welcomed a baby","welcomed our child","new son","new daughter",
            "i gave birth","i've given birth","my child was born","our child was born",
            "just had a baby","recently had a baby"
        ],
        "docs": [
            "Birth certificate",
            "Bank details for tax refund",
            "Payslips / PAYG summary",
            "Receipts for child-related expenses"
        ],
        "tax_actions": [
            "Add dependent in tax return",
            "Check family-related offsets",
            "Keep receipts for childcare/medical"
        ]
    },
    "job_loss": {
        "keywords": [
            "lost job","job loss","laid off","made redundant","redundant","terminated",
            "fired","got fired","let go","out of work","unemployed","no longer employed",
            "i lost my job","i was laid off","i've been laid off","i was made redundant",
            "i got fired","they let me go","my role was terminated","i became unemployed",
            "between jobs","without a job","job ended","contract ended"
        ],
        "docs": [
            "Termination letter",
            "Final payslip / PAYG summary",
            "Redundancy payment statement",
            "Superannuation details"
        ],
        "tax_actions": [
            "Update income estimate",
            "Check income support",
            "Track redundancy payments"
        ]
    },
    "freelance": {
        "keywords": [
            "freelance","freelancer","contractor","consultant","self employed","self-employed",
            "sole trader","independent contractor","independent worker",
            "gig work","gig economy","side gig","side hustle",
            "i'm freelancing","i am freelancing","i started freelancing","i started a freelance business",
            "i became a contractor","i work as a contractor","doing gigs","taking gigs",
            "registered an abn","have an abn","got an abn"
        ],
        "docs": [
            "ABN/TFN registration",
            "Invoices / contracts",
            "Income–expense records",
            "Receipts for tools/equipment/software"
        ],
        "tax_actions": [
            "Track business income/expenses",
            "Check GST obligations",
            "Put aside money for tax"
        ]
    },
    "moving_house": {
        "keywords": [
            "moved house","moving house","moved home","relocated","relocation",
            "changed address","change address","new address","moved interstate","moving interstate",
            "i moved to a new address","i moved to sydney","i relocated to melbourne",
            "we changed our address","we have a new address","i shifted house"
        ],
        "docs": [
            "Proof of new address",
            "Update employer details",
            "Lease or purchase agreement"
        ],
        "tax_actions": [
            "Update address in records",
            "Check for local entitlements"
        ]
    },
    "marriage_divorce": {
        "keywords": [
            "married","got married","we married","tied the knot","got hitched",
            "new spouse","husband","wife","partner","marital status change",
            "divorce","divorced","separated","separation","split up","we separated",
            "i got married","we got married last month","we just married",
            "we separated recently","we went through a divorce"
        ],
        "docs": [
            "Marriage or divorce certificate",
            "Updated marital status in records",
            "Dependent details if children",
            "Joint account information"
        ],
        "tax_actions": [
            "Update dependents and offsets",
            "Adjust withholding if household income changed"
        ]
    },
    "back_to_work": {
        "keywords": [
            "back to work","returning to work","returned to work","went back to work",
            "started a new job","starting a new job","new job","got hired","rehired",
            "employment resumed","rejoined workforce","back at work","returning after leave",
            "i started a new job","i just started work","i returned after parental leave",
            "i'm back to work","i am back to work"
        ],
        "docs": [
            "New employment contract",
            "Payslips / PAYG summary",
            "Superannuation details"
        ],
        "tax_actions": [
            "Review deductions",
            "Update tax withholding",
            "Update contact/bank details"
        ]
    },
    "retirement": {
        "keywords": [
            "retired","retirement","stop working","stopped working","finished working",
            "no longer working","left work","ended career","retiring","i retired",
            "i have retired","i'm retired","i stopped working","i finished working"
        ],
        "docs": [
            "Superannuation payout details",
            "Pension or annuity statements",
            "Centrelink / Age pension info"
        ],
        "tax_actions": [
            "Report super/pension income",
            "Check age pension/offsets"
        ]
    },
    "studying": {
        "keywords": [
            "studying","study","student","started studying","going back to school",
            "returned to study","commenced study","enrolled in uni","university","college",
            "hecs","help loan","student loan","os-help","i started studying","i am studying",
            "i went back to uni","i'm a student"
        ],
        "docs": [
            "HECS/HELP statement",
            "Enrolment confirmation",
            "Receipts for course-related expenses"
        ],
        "tax_actions": [
            "Check HECS/HELP repayments",
            "Keep receipts for self-education"
        ]
    }
}

# =========================
# NLU: Detect life events
# =========================
def detect_events(user_text, threshold=60):
    text = (user_text or "").lower()
    results = []
    for ev_key, meta in LIFE_EVENTS.items():
        best_kw, best_sc, best_phrase = None, -1, ""
        for kw in meta["keywords"]:
            sc = fuzz.partial_ratio(text, kw.lower())
            if sc > best_sc:
                best_sc = sc
                best_kw = kw
                m = re.search(rf"\b{re.escape(kw.lower())}\b", text)
                best_phrase = user_text[m.start():m.end()] if m else kw
        if best_sc >= threshold:
            results.append((ev_key, best_kw, int(best_sc), best_phrase))
    results.sort(key=lambda x: x[2], reverse=True)
    return results

# =========================
# Calendar parsing
# =========================
CAL_YES = {"yes","y","ok","okay","sure","save","save it","please save","đồng ý","lưu","lưu lại","có"}

def _extract_time(text):
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if m:
        return f"{m.group(1).zfill(2)}:{m.group(2)}"
    m = re.search(r"\b([1-9]|1[0-2])\s*(am|pm)\b", text, flags=re.I)
    if m:
        h = int(m.group(1)); ampm = m.group(2).lower()
        if ampm == "pm" and h != 12: h += 12
        if ampm == "am" and h == 12: h = 0
        return f"{h:02d}:00"
    return ""

def parse_datetime(text):
    if not text: return None, None
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", _extract_time(text)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-{d:02d}", _extract_time(text)
    res = search_dates(
        text, languages=["en"],
        settings={
            "RETURN_AS_TIMEZONE_AWARE": False,
            "PREFER_DATES_FROM": "future",
            "SKIP_TOKENS": ["to", "ok", "okay", "save", "calendar"]
        }
    )
    if not res: return None, None
    picked = None
    for frag, dt in res:
        if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", frag):
            picked = dt; break
    if picked is None: picked = res[0][1]
    return picked.strftime("%Y-%m-%d"), _extract_time(text)

def parse_calendar_command(text):
    if not text: return None, None, None
    if re.search(r"\b(remind me|set (a )?reminder|save (it )?to (the )?calendar)\b", text, flags=re.I):
        parts = re.split(r"\s+to\s+", text, maxsplit=1, flags=re.I)
        date_part = parts[0]
        title = "Tax reminder"
        if len(parts) > 1 and parts[1].strip():
            title = parts[1].strip()
        d, t = parse_datetime(date_part)
        return title, d, t
    return None, None, None

def make_event_intro(ev_key, phrase):
    if ev_key == "new_baby": return f"Congrats on your new baby 🎉 (I noticed “{phrase}”)."
    if ev_key == "job_loss": return f"Sorry to hear about the job loss 💼 (I picked up “{phrase}”)."
    if ev_key == "freelance": return f"Starting freelance can be exciting 🚀 (I matched “{phrase}”)."
    if ev_key == "moving_house": return f"Moving house is a big change 📦 (I caught “{phrase}”)."
    if ev_key == "marriage_divorce": return f"Life events like marriage or divorce can affect taxes 💍 (from “{phrase}”)."
    if ev_key == "back_to_work": return f"Welcome back to work 👔 (keyword: “{phrase}”)."
    if ev_key == "retirement": return f"Congratulations on your retirement 🏖 (matched “{phrase}”)."
    if ev_key == "studying": return f"Good luck with your studies 🎓 (I noticed “{phrase}”)."
    return f"I detected **{ev_key}** from “{phrase}”."

# =========================
# Chatbot core
# =========================
def chatbot_response(message, history, memory_events, show_debug, reminders, pending):
    if not (message and str(message).strip()):
        return "Please type something so I can help 🙂", memory_events, reminders, pending
    text = message.strip()

    # 1) Confirm pending reminder
    if pending and text.lower() in CAL_YES:
        tasks = reminders or []
        new_id = (max([t["id"] for t in tasks], default=0) + 1)
        tasks.append({
            "id": new_id,
            "title": pending.get("title","Tax reminder"),
            "date": pending.get("date",""),
            "time": pending.get("time",""),
            "notes": pending.get("notes",""),
            "done": False
        })
        reminders = tasks
        saved = pending
        pending = None
        date_show = saved.get("date","(no date)")
        time_show = (" " + saved["time"]) if saved.get("time") else ""
        reply = f"✅ Saved to calendar: **{saved.get('title','Reminder')}** — {date_show}{time_show}"
        return reply, memory_events, reminders, pending

    # 2) Direct calendar command
    title_cmd, date_cmd, time_cmd = parse_calendar_command(text)
    if title_cmd and (date_cmd or time_cmd):
        tasks = reminders or []
        new_id = (max([t["id"] for t in tasks], default=0) + 1)
        tasks.append({
            "id": new_id,
            "title": title_cmd,
            "date": date_cmd or "",
            "time": time_cmd or "",
            "notes": "",
            "done": False
        })
        reminders = tasks
        ds = date_cmd or "(no date)"
        ts = (" " + time_cmd) if time_cmd else ""
        reply = f"✅ Reminder created: **{title_cmd}** — {ds}{ts}"
        return reply, memory_events, reminders, None

    # 3) Detect events
    matches = detect_events(text, threshold=60)

    if not matches and not memory_events:
        date_hint, time_hint = parse_datetime(text)
        if date_hint or time_hint:
            pending = {"title": "Tax reminder", "date": date_hint or "", "time": time_hint or "", "notes": ""}
            ask = f"\n\n📅 I found a date {date_hint or ''} {time_hint or ''}. Save to calendar?"
            return "I couldn’t recognise a life event yet." + ask, memory_events, reminders, pending
        return "Hmm, I couldn’t recognise a life event 🤔. Try 'I lost my job' or 'I had a baby'.", memory_events, reminders, pending

    parts = []
    if matches:
        for ev_key, kw, sc, phrase in matches:
            if ev_key not in memory_events:
                memory_events.append(ev_key)
        for ev_key, kw, sc, phrase in matches:
            plan = LIFE_EVENTS[ev_key]
            intro = make_event_intro(ev_key, phrase)
            docs = "\n".join(f"- {d}" for d in plan["docs"])
            acts = "\n".join(f"- {a}" for a in plan["tax_actions"])
            section = intro + "\n\n**Documents you should gather:**\n" + docs
            section += "\n\n**Tax-related steps (simple):**\n" + acts
            if show_debug:
                section += f"\n\n_(Matched keyword: `{kw}`, score={sc})_"
            parts.append(section)
    else:
        parts.append("I’ll reuse what we talked about earlier — here are the checklists again:")
        for ev_key in memory_events:
            plan = LIFE_EVENTS[ev_key]
            docs = "\n".join(f"- {d}" for d in plan["docs"])
            acts = "\n".join(f"- {a}" for a in plan["tax_actions"])
            parts.append(f"\n**Event remembered: {ev_key.replace('_',' ')}**\nDocuments:\n{docs}\n\nTax steps:\n{acts}")

    date_hint, time_hint = parse_datetime(text)
    if date_hint or time_hint:
        default_title = {
            "new_baby":"Prepare baby-related documents",
            "job_loss":"Prepare documents after job loss",
            "freelance":"Prepare freelance tax documents",
            "moving_house":"Update address & documents",
            "marriage_divorce":"Update marital status & records",
            "back_to_work":"Checklist for returning to work",
            "retirement":"Prepare retirement income records",
            "studying":"Self-education/HECS checklist"
        }
        suggested = default_title.get(matches[0][0], "Tax reminder") if matches else "Tax reminder"
        pending = {"title": suggested, "date": date_hint or "", "time": time_hint or "", "notes": ""}
        parts.append(f"\n📅 I noticed a date {date_hint or ''} {time_hint or ''}. Save to calendar? (reply **yes** to confirm)")

    reply = "\n\n---\n\n".join(parts) if parts else "I didn’t find anything useful this time 🤔."
    return reply, memory_events, reminders, pending

# =========================
# Calendar UI helpers
# =========================
def render_task_list(tasks):
    if not tasks:
        return "No reminders yet."
    def srt(t):
        d = t.get("date") or "9999-12-31"
        tm = t.get("time") or "23:59"
        return (t.get("done", False), d, tm)
    tasks = sorted(tasks, key=srt)
    by_date = {}
    for t in tasks:
        d = t.get("date") or "(no date)"
        by_date.setdefault(d, []).append(t)
    lines = []
    for d in sorted(by_date.keys()):
        lines.append(f"### {d}")
        for t in by_date[d]:
            status = "✅ Done" if t.get("done") else "⏳ Pending"
            time_part = f" {t['time']}" if t.get("time") else ""
            lines.append(f"- [{status}] **{t.get('title','(no title)')}** —{time_part}")
    return "\n".join(lines)

def _opts(tasks):
    return [f"{t['id']} — {t['title']} ({t.get('date','')}{' '+t['time'] if t.get('time') else ''})" for t in (tasks or [])]

def _validate_date_str(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except Exception:
        return False

def add_task(title, date_str, time_str, notes, tasks):
    title = (title or "").strip()
    if not title:
        return tasks, "❌ Please enter a title.", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)
    date_str = (date_str or "").strip()
    if not date_str or not _validate_date_str(date_str):
        return tasks, "❌ Date must be in YYYY-MM-DD (e.g., 2025-09-10).", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)
    time_str = (time_str or "").strip()
    if time_str and len(time_str.split(":")) != 2:
        return tasks, "❌ Time must be HH:MM (e.g., 09:00).", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)
    new_id = (max([t["id"] for t in tasks], default=0) + 1) if tasks else 1
    new_task = {"id": new_id, "title": title, "date": date_str, "time": time_str, "notes": (notes or "").strip(), "done": False}
    tasks = (tasks or []) + [new_task]
    return tasks, "✅ Added.", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)

def toggle_task(selected_label, tasks):
    if not tasks:
        return tasks, "No reminders.", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)
    if not selected_label:
        return tasks, "Select one first.", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)
    tid = int(str(selected_label).split(" — ")[0])
    for t in tasks:
        if t["id"] == tid:
            t["done"] = not t.get("done", False)
            break
    return tasks, "✅ Toggled.", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)

def delete_task(selected_label, tasks):
    if not tasks:
        return tasks, "No reminders.", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)
    if not selected_label:
        return tasks, "Select one first.", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)
    tid = int(str(selected_label).split(" — ")[0])
    tasks = [t for t in tasks if t["id"] != tid]
    return tasks, "🗑️ Deleted.", render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)

def to_chat():
    return gr.update(visible=True), gr.update(visible=False)

def to_calendar():
    return gr.update(visible=False), gr.update(visible=True)

def refresh(tasks):
    return render_task_list(tasks), gr.update(choices=_opts(tasks), value=None)

def build_logo_html(path="assets/govmate_logo.png", max_h=80):
    if not os.path.exists(path):
        return "<div/>"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"<img src='data:image/png;base64,{b64}' style='max-height:{max_h}px;width:auto;object-fit:contain;'/>"

logo_html = build_logo_html("assets/govmate_logo.png", max_h=80)

# =========================
# UI
# =========================
with gr.Blocks(css="""
.header{
  display:grid;
  grid-template-columns: 1fr auto 1fr;
  align-items:center;
  gap:12px;
}
.header-left  { justify-self:start;  text-align:left; }
.header-center{ justify-self:center; text-align:center; }
.header-right { justify-self:end;    text-align:right; display:flex; gap:8px; }
.header h1 { margin: 10px 0; }
#btn-chat, #btn-cal { min-width:44px; height:40px; font-size:18px; }
""") as demo:

    with gr.Row(elem_classes="header"):
        with gr.Column(scale=1, elem_classes="header-left"):
            gr.Markdown("## TaxPrep Chatbot (Gradio Demo)")
        with gr.Column(scale=1, elem_classes="header-center"):
            gr.HTML(logo_html)
        with gr.Column(scale=1, elem_classes="header-right"):
            with gr.Row():
                btn_chat = gr.Button(value="💬", elem_id="btn-chat", variant="secondary")
                btn_cal  = gr.Button(value="📅", elem_id="btn-cal",  variant="secondary")

    memory_events   = gr.State(value=[])
    reminders_state = gr.State(value=[])
    pending_state   = gr.State(value=None)

    with gr.Group(visible=True) as chat_view:
        show_debug = gr.Checkbox(label="Show matching details", value=True)
        gr.ChatInterface(
            fn=chatbot_response,
            additional_inputs=[memory_events, show_debug, reminders_state, pending_state],
            additional_outputs=[memory_events, reminders_state, pending_state],
            title="TaxPrep Chatbot",
            description=(
                "💡 Quick Guide:\n"
                "- Use simple English.\n"
                "- Mention life events: baby 👶, job loss 💼, freelance 🚀, moving house 📦, "
                "marriage/divorce 💍, back to work 👔, retirement 🏖, studying 🎓.\n"
                "- You can mention multiple events in one message.\n"
                "- Or: 'remind me on 2025-09-10 at 09:00 to lodge tax return'."
            ),
            type="messages"
        )

    with gr.Group(visible=False) as cal_view_group:
        gr.Markdown("### Your Reminders (grouped by date)")
        cal_md = gr.Markdown(render_task_list([]))
        refresh_btn = gr.Button("Refresh now", variant="secondary")

        with gr.Row():
            title_in = gr.Textbox(label="Title", placeholder="e.g., Lodge tax return – prepare documents")
        with gr.Row():
            date_in  = gr.Textbox(label="Date (YYYY-MM-DD)", placeholder="2025-09-10")
            time_in  = gr.Textbox(label="Time (HH:MM, optional)", placeholder="09:00")
        notes_in = gr.Textbox(label="Notes (optional)", lines=2)

        add_btn = gr.Button("Add")
        status  = gr.Markdown()

        select_dd = gr.Dropdown(choices=[], label="Select a reminder")
        with gr.Row():
            toggle_btn = gr.Button("Mark as Done/Undone")
            delete_btn = gr.Button("Delete")

        add_btn.click(add_task, inputs=[title_in, date_in, time_in, notes_in, reminders_state],
                      outputs=[reminders_state, status, cal_md, select_dd])
        toggle_btn.click(toggle_task, inputs=[select_dd, reminders_state],
                         outputs=[reminders_state, status, cal_md, select_dd])
        delete_btn.click(delete_task, inputs=[select_dd, reminders_state],
                         outputs=[reminders_state, status, cal_md, select_dd])
        refresh_btn.click(refresh, inputs=[reminders_state], outputs=[cal_md, select_dd])

    btn_chat.click(to_chat, outputs=[chat_view, cal_view_group]).then(refresh, inputs=[reminders_state], outputs=[cal_md, select_dd])
    btn_cal.click(to_calendar, outputs=[chat_view, cal_view_group]).then(refresh, inputs=[reminders_state], outputs=[cal_md, select_dd])

if __name__ == "__main__":
    demo.launch()
