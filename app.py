import pandas as pd

import re
import dateparser
from dateparser.search import search_dates
from datetime import datetime

import gradio as gr
from datetime import datetime
import base64, os

from collections import defaultdict

keyword_table = pd.read_excel(
    'rules.xlsx',
    sheet_name='Keyword definition',
)

CAL_YES = {"yes","y","ok","okay","sure","save","save it","please save"}

def _extract_time(text):
    """Find a time string (HH:MM or '9am/9 pm') in the text."""
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
    if m: return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", _extract_time(text)

    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-{d:02d}", _extract_time(text)

    res = search_dates(
        text, languages=["en"],
        settings={"RETURN_AS_TIMEZONE_AWARE": False, "PREFER_DATES_FROM": "future",
                  "SKIP_TOKENS": ["to", "ok", "okay", "save", "calendar"]}
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
        title = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "Tax reminder"
        d, t = parse_datetime(date_part)
        return title, d, t
    return None, None, None

def split_variants(variants_cell: str):
    parts = [v.strip().lower() for v in str(variants_cell).split("|") if str(v).strip()]
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            out.append(p); seen.add(p)
    return out

def find_keyword_hits(user_text: str):
    text_low = (user_text or "").lower()
    hits = []
    for i, row in keyword_table.iterrows():
        ev = str(row["event_key"]).strip()
        key = str(row["keyword_key"]).strip()
        variants = split_variants(row["keyword_variants"])
        matched_vars = []
        for v in variants:
            pattern = r"\b" + re.escape(v) + r"\b"
            if re.search(pattern, text_low, flags=re.IGNORECASE):
                matched_vars.append(v)
        if matched_vars:
            hits.append((i, ev, key, matched_vars))
    hits.sort(key=lambda x: x[0])
    return hits

def select_top_event(hits):
    if not hits: return [], None
    count_by_event, first_row = defaultdict(int), {}
    for idx, ev, _, _ in hits:
        count_by_event[ev] += 1
        if ev not in first_row: first_row[ev] = idx
    top_event = max(count_by_event.keys(), key=lambda e: (count_by_event[e], -first_row[e]))
    selected = [h for h in hits if h[1] == top_event]
    selected.sort(key=lambda x: x[0])
    return selected, top_event

def render_sources(rows_dicts):
    seen = set()
    lines = ["**Sources:**"]
    for r in rows_dicts:
        url = str(r.get("source_url","")).strip()
        if url and url not in seen:
            title = (r.get("source_title") or "Official source").strip()
            upd = str(r.get("updated_at","")).strip()
            if upd and upd.lower() != "nat":
                lines.append(f"- {title} (updated {upd}): {url}")
            else:
                lines.append(f"- {title}: {url}")
            seen.add(url)
    return "\n".join(lines) if seen else ""

FRIENDLY_INTRO = {
    "newborn_baby": "Congratulations on your new baby! 🎉 Let’s make the admin side feel easy.",
    "new_baby_documents": "Congrats on your little one! 👶 Here’s a tidy checklist for the paperwork.",
    "new_baby_income_check": "Happy news! With a new baby, a few money and tax settings are worth checking.",
    "starting_new_job": "Congrats on the new role! 👔 I’ll help you get through the onboarding bits quickly.",
    "redundancy_jobseeker": "I’m sorry to hear about the redundancy 💼 — here’s a practical plan to move forward.",
    "redundancy_actions": "Let’s sort your payout, tax, and next steps after redundancy.",
    "contractor_start": "Exciting shift to contracting! 🚀 Here’s how to set it up cleanly.",
    "sole_trader_setup": "Going sole trader? Here’s the simple path to get started.",
    "start_business_overview": "Starting a business — great! Here’s the one-page playbook.",
    "small_business_setup": "Small business setup — we’ll keep it simple and compliant.",
    "citizenship_tax": "Congrats on becoming an Australian citizen! 🇦🇺 A few tax settings might change.",
    "disaster_recovery_support": "That sounds really tough. Here’s the official help available right now.",
    "departing_australia_super": "Leaving Australia? Let’s check your super (DASP) and how tax works.",
    "family_domestic_violence_support": "You’re not alone. Here are confidential supports and payments that can help.",
    "first_home_temp_visa": "Thinking about your first home on a temporary visa? Here’s what to know early.",
    "work_related_deductions": "Doing your tax? Here’s a quick guide to common work-related deductions.",
    "graduate_job_search": "Fresh graduate — nice! Here are free tools and programs to land that first role.",
}

def pretty_event_title(ev_key: str) -> str:
    mapping = {
        "newborn_baby": "New baby — what to do next",
        "new_baby_documents": "New baby — documents & registrations",
        "new_baby_income_check": "New baby — payments & income checks",
        "starting_new_job": "Starting a new job",
        "redundancy_jobseeker": "Redundancy — JobSeeker & support",
        "redundancy_actions": "Redundancy — tax & admin",
        "contractor_start": "Starting as a contractor",
        "sole_trader_setup": "Sole trader setup",
        "start_business_overview": "Starting a business — overview",
        "small_business_setup": "Small business setup",
        "citizenship_tax": "Citizenship — tax settings",
        "disaster_recovery_support": "Disaster recovery support",
        "departing_australia_super": "Departing Australia — super (DASP)",
        "family_domestic_violence_support": "Family & domestic violence support",
        "first_home_temp_visa": "First home on a temporary visa",
        "work_related_deductions": "Work-related deductions",
        "graduate_job_search": "Graduate — job search",
    }
    return mapping.get(ev_key, "Here’s a quick plan")

def compose_answer_from_rows(selected_hits, show_debug=False):
    """
    Conversational answer:
    - Gentle intro based on event_key
    - Friendly heading
    - Bullet checklist from 'short_answer'
    - Sources at the end
    """
    if not selected_hits:
        return "I couldn’t recognise a relevant topic yet."

    rows = [keyword_table.iloc[i].to_dict() for (i, ev, _, _) in selected_hits]
    event_key = selected_hits[0][1]
    intro = FRIENDLY_INTRO.get(event_key, "Here’s a quick, friendly checklist to help you move forward.")
    heading = pretty_event_title(event_key)

    bullets = []
    for r in rows:
        ans = str(r.get("short_answer","")).strip()
        if ans:
            bullets.append(f"- {ans}")

    parts = []
    parts.append(f"{intro}\n\n**{heading}**\n\n" + "\n".join(bullets))

    src_md = render_sources(rows)
    if src_md:
        parts.append("\n\n" + src_md)

    if show_debug:
        dbg = [f"`{keyword_table.iloc[i]['keyword_key']}` ⇢ {', '.join(matched)}"
               for (i, _, _, matched) in selected_hits]
        parts.append("\n\n_" + " | ".join(dbg) + "_")

    parts.append("\n\nIf you’d like, I can save a reminder for any dates or deadlines — just say something like *“remind me on 2025-09-10 to lodge my return”*.")

    return "".join(parts)

def chatbot_response(message, history, memory_events, show_debug, reminders, pending):
    if not (message and str(message).strip()):
        return "Please type something so I can help 🙂", memory_events, reminders, pending

    text = message.strip()

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
        reply = f"✅ Saved to calendar: **{saved.get('title','Reminder')}** — {date_show}{time_show}\n\nYou can open the 📅 tab any time to view or edit."
        return reply, memory_events, reminders, pending

    title_cmd, date_cmd, time_cmd = parse_calendar_command(text)
    if title_cmd and (date_cmd or time_cmd):
        tasks = reminders or []
        new_id = (max([t["id"] for t in tasks], default=0) + 1)
        new_task = {
            "id": new_id,
            "title": title_cmd,
            "date": date_cmd or "",
            "time": time_cmd or "",
            "notes": "",
            "done": False
        }
        tasks.append(new_task)
        reminders = tasks
        ds = new_task["date"] or "(no date)"
        ts = (" " + new_task["time"]) if new_task["time"] else ""
        reply = f"✅ Saved to calendar: **{new_task['title']}** — {ds}{ts}\n\nI’ve added it to your reminders. You can manage it in the 📅 tab."
        return reply, memory_events, reminders, None
    elif title_cmd or date_cmd or time_cmd:
        return ("I can save that, but I need a date or time (e.g., **2025-09-10 09:00**). "
                "Try: *remind me on 2025-09-10 at 09:00 to lodge my tax return*."), memory_events, reminders, pending

    all_hits = find_keyword_hits(text)
    selected_hits, chosen_event = select_top_event(all_hits)

    if not selected_hits and not memory_events:
        date_hint, time_hint = parse_datetime(text)
        if date_hint or time_hint:
            pending = {"title": "Tax reminder", "date": date_hint or "", "time": time_hint or "", "notes": ""}
            ask = f"\n\n📅 I found a date {date_hint or ''} {time_hint or ''}. Save to calendar? (reply **yes** to confirm)"
            return "I couldn’t recognise a keyword yet." + ask, memory_events, reminders, pending
        return ("I’m not sure I caught the topic 🤔. "
                "Try something like *“we just had a baby”*, *“I’m starting a new job”*, or *“I was made redundant”*."), memory_events, reminders, pending

    if chosen_event and chosen_event not in memory_events:
        memory_events.append(chosen_event)

    reply = compose_answer_from_rows(selected_hits, show_debug=bool(show_debug))

    date_hint, time_hint = parse_datetime(text)
    if date_hint or time_hint:
        pending = {"title": "Tax reminder", "date": date_hint or "", "time": time_hint or "", "notes": ""}
        reply += f"\n\n📅 I noticed a date {date_hint or ''} {time_hint or ''}. Save to calendar? (reply **yes** to confirm)"

    return reply, memory_events, reminders, pending

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

def build_logo_html(path="/content/govmate_logo.png", max_h=80):
    if not os.path.exists(path):
        return "<div/>"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"<img src='data:image/png;base64,{b64}' style='max-height:{max_h}px;width:auto;object-fit:contain;'/>"

logo_html = build_logo_html("assets/govmate_logo.png", max_h=80)

with gr.Blocks(css="""
/* Header */
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

/* ⬆️ Bigger CHATBOT (messages) area only */
#chat_view .gr-chatbot { height: 80vh !important; min-height: 560px; }
#chat_view .gr-chatbot .overflow-y-auto { max-height: 80vh !important; }

/* ⬇️ Keep the USER INPUT compact (don’t enlarge it) */
#chat_view .gr-textbox textarea { min-height: 36px; max-height: 120px; }
""") as demo:

    with gr.Row(elem_classes="header"):
        with gr.Column(scale=1, elem_classes="header-left"):
            gr.Markdown("## MyGovMate Chatbot")
        with gr.Column(scale=1, elem_classes="header-center"):
            gr.HTML(logo_html)
        with gr.Column(scale=1, elem_classes="header-right"):
            with gr.Row():
                btn_chat = gr.Button(value="💬", elem_id="btn-chat", variant="secondary")
                btn_cal  = gr.Button(value="📅", elem_id="btn-cal",  variant="secondary")

    memory_events   = gr.State(value=[])
    reminders_state = gr.State(value=[])
    pending_state   = gr.State(value=None)
    show_debug_state = gr.State(value=False)

    with gr.Group(visible=True, elem_id="chat_view") as chat_view:
        gr.ChatInterface(
            fn=chatbot_response,
            additional_inputs=[memory_events, show_debug_state, reminders_state, pending_state],
            additional_outputs=[memory_events, reminders_state, pending_state],
            title="MyGovMate",
            description=(
                "💡 Quick Guide:\n"
                " - Please use simple English.\n"
                " - Please mention your life events : new baby 👶, job loss 💼, freelance 🚀, moving house 📦, marriage/divorce 💍, back to work 👔, retirement 🏖, studying 🎓.\n"
                " - To save a reminder, please try: 'remind me on 2025-09-10 at 09:00 to lodge tax return'."
            ),
            type="messages"
        )

    with gr.Group(visible=False) as cal_view_group:
        gr.Markdown("### Your Reminders")
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

    btn_chat.click(to_chat, inputs=[], outputs=[chat_view, cal_view_group]) \
             .then(refresh, inputs=[reminders_state], outputs=[cal_md, select_dd])
    btn_cal.click(to_calendar, inputs=[], outputs=[chat_view, cal_view_group]) \
            .then(refresh, inputs=[reminders_state], outputs=[cal_md, select_dd])

    demo.load(lambda t: (render_task_list(t), gr.update(choices=_opts(t), value=None)),
              inputs=[reminders_state], outputs=[cal_md, select_dd])

demo.launch(share=True, debug=True)