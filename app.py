"""PROMO FINDER web app.   Run locally:  streamlit run app.py

The app is a thin screen over promo_finder.pipeline: it collects the input and settings,
calls pipeline.run(), and shows the result. Everything it shows comes from that run.

Security notes
- AI mode uses the visitor's own API key for the current browser session.
- The key is not written to a project file, log or download.
- Uploads are parsed from memory; ZIP contents are not extracted to disk.
- Each browser session gets a separate temporary cache directory, removed with
  "Clear session".
"""
import hashlib, html, json, os, shutil, tempfile, time

import altair as alt
import pandas as pd
import streamlit as st

from promo_finder import pipeline, rules
from promo_finder.config import Settings
from promo_finder.inputs import InputError, parse_all
from promo_finder.llm import LLM, PRESETS
from promo_finder.parse import fmt

MAX_AI_EPISODES = 60          # one AI run in the public app covers at most this many episodes
MAX_AI_RUNS_PER_SESSION = 5   # so one visitor cannot burn through a key by accident

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
  .block-container {padding-top: 3.6rem; max-width: 1200px;}
  h1, h2, h3 {letter-spacing: -0.01em;}
  .pf-hero {padding: 1.6rem 1.8rem; border-radius: 16px; background: linear-gradient(135deg, #1f1630 0%, #3b1d3f 100%);
            color: #f6f1f7; margin-bottom: 1.2rem;}
  .pf-hero .pf-name {font-size: 2.1rem; font-weight: 800; letter-spacing: 0.08em; margin: 0;}
  .pf-hero .pf-sub {font-size: 1.1rem; opacity: 0.9; margin: 0.2rem 0 0.8rem 0;}
  .pf-hero p {font-size: 0.97rem; opacity: 0.85; max-width: 760px; margin: 0;}
  .pf-flow {display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.6rem 0 0.4rem 0;}
  .pf-step {flex: 1 1 118px; border: 1px solid rgba(128,128,128,0.28); border-radius: 12px; padding: 0.7rem 0.8rem;
            background: rgba(128,128,128,0.06);}
  .pf-step b {display: block; font-size: 0.92rem;}
  .pf-step span {font-size: 0.8rem; opacity: 0.8;}
  .pf-tag {display: inline-block; font-size: 0.68rem; font-weight: 700; padding: 1px 7px; border-radius: 99px;
           margin-bottom: 4px; letter-spacing: 0.04em;}
  .pf-ai {background: #f3d9ec; color: #8a1557;}
  .pf-code {background: #dbeafe; color: #1e40af;}
  .pf-card {border: 1px solid rgba(128,128,128,0.25); border-radius: 14px; padding: 1rem 1.1rem; margin-bottom: 0.8rem;}
  .pf-quote {font-size: 1.02rem; border-left: 3px solid #be185d; padding-left: 0.7rem; margin: 0.3rem 0 0.6rem 0;}
  .pf-muted {opacity: 0.75; font-size: 0.88rem;}
  div[data-testid="stMetricValue"] {font-size: 1.55rem;}
</style>
""", unsafe_allow_html=True)


def session_dir():
    if "work" not in st.session_state or not os.path.isdir(st.session_state["work"]):
        st.session_state["work"] = tempfile.mkdtemp(prefix="promo_finder_")
    return st.session_state["work"]


def clear_session():
    shutil.rmtree(st.session_state.get("work", ""), ignore_errors=True)
    for k in list(st.session_state):
        del st.session_state[k]


# ============================================================== landing
st.markdown("""
<div class="pf-hero">
  <div class="pf-name">PROMO FINDER</div>
  <div class="pf-sub">AI-powered microdrama promo discovery and ranking</div>
  <p>Upload a show's screenplays. The tool searches every legal 20-60 second clip, keeps the ones that open on a
  hook and end on a cliffhanger, checks whether a stranger could follow them, and returns exact timestamps in the
  order to post them. It reads scripts only: no video is analysed.</p>
</div>
""", unsafe_allow_html=True)

st.markdown("""
<div class="pf-flow">
  <div class="pf-step"><span class="pf-tag pf-code">CODE</span><b>1. Parse</b><span>Scripts become timestamped lines</span></div>
  <div class="pf-step"><span class="pf-tag pf-ai">AI</span><b>2. Rate lines</b><span>Hook and cliffhanger strength, 0-3</span></div>
  <div class="pf-step"><span class="pf-tag pf-code">CODE</span><b>3. Search</b><span>Every legal clip, clean cut points</span></div>
  <div class="pf-step"><span class="pf-tag pf-ai">AI</span><b>4. Stranger test</b><span>Would a new viewer follow it?</span></div>
  <div class="pf-step"><span class="pf-tag pf-code">CODE</span><b>5. Audit</b><span>Rule-breaking clips are rejected</span></div>
  <div class="pf-step"><span class="pf-tag pf-code">CODE</span><b>6. Rank</b><span>Score and a varied posting order</span></div>
</div>
<p class="pf-muted">The AI never writes a timestamp: it only points at script lines, and code looks the times up.
Keyword mode replaces both AI steps with a simple word count and needs no API key.</p>
""", unsafe_allow_html=True)

if not st.session_state.get("started"):
    c1, c2 = st.columns([1, 3])
    if c1.button("Start analysing", type="primary", width="stretch"):
        st.session_state["started"] = True
        st.rerun()
    c2.caption("Accepted input: one .zip of screenplay .txt files, or several .txt files. Up to 500 files, "
               "2 MB per file. Each file needs blocks like `**NAME:**` then `Start: (mm:ss.ff) | End: (mm:ss.ff)`.")
    st.stop()

# ============================================================== input workspace
st.header("1. Input")
left, right = st.columns([1.15, 1], gap="large")

with left:
    uploads = st.file_uploader("Screenplays: one .zip, or several .txt files", type=["zip", "txt"],
                               accept_multiple_files=True, key="uploads")
    DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples", "demo-episode-001.txt")
    if not uploads and os.path.exists(DEMO):
        if st.button("No screenplays to hand? Use an invented demo episode"):
            st.session_state["use_demo"] = True
    parsed = None
    raw = None
    if uploads:
        st.session_state["use_demo"] = False
        raw = [(u.name, u.getvalue()) for u in uploads]
    elif st.session_state.get("use_demo"):
        st.info("Using the invented demo episode (not Kuku content). Upload files to replace it.")
        raw = [("demo-episode-001.txt", open(DEMO, "rb").read())]
    if raw:
        fingerprint = hashlib.sha256(b"".join(n.encode() + d for n, d in raw)).hexdigest()
        if st.session_state.get("fingerprint") != fingerprint:
            st.session_state.update(fingerprint=fingerprint, result=None)
            try:
                st.session_state["parsed"] = ("ok", parse_all(raw), raw)
            except InputError as e:
                st.session_state["parsed"] = ("error", str(e), None)
        parsed = st.session_state.get("parsed")
    if parsed and parsed[0] == "error":
        st.error(f"This input cannot be used: {parsed[1]}")
    elif parsed:
        episodes, report = parsed[1]
        n_warn = sum(len(w) for w in report["warnings"].values())
        a, b, c, d = st.columns(4)
        a.metric("Files", report["files_read"])
        b.metric("Episodes", report["parsed"])
        c.metric("Beats", f"{report['beats']:,}")
        d.metric("Warnings", n_warn)
        problems = len(report["unusable"]) + len(report["duplicates"])
        if problems or n_warn:
            st.warning(f"Ready with caveats: {len(report['unusable'])} unusable file(s), "
                       f"{len(report['duplicates'])} duplicate episode(s), {n_warn} parse warning(s). Details below.")
        else:
            st.success(f"Ready: {report['parsed']} episodes parsed, every screenplay block accounted for "
                       f"({report['blocks']:,} blocks, {report['beats']:,} beats).")
        table = pd.DataFrame([{"Episode": e["episode"], "File": e["file"], "Beats": len(e["beats"]),
                               "Length": fmt(e["duration"]),
                               "Scenes": len({x.scene for x in e["beats"]}),
                               "Status": "warnings" if e["warnings"] else "ok"} for e in episodes.values()])
        with st.expander(f"Detected files ({len(table)})", expanded=False):
            st.dataframe(table, hide_index=True, width="stretch", height=260)
        if report["unusable"] or report["duplicates"] or report["skipped"] or report["warnings"]:
            with st.expander("Problems found while reading", expanded=bool(problems)):
                for name, why in report["unusable"]:
                    st.write(f"- **{name}**: unusable ({why})")
                for name, ep, kept in report["duplicates"]:
                    st.write(f"- **{name}**: duplicate of episode {ep} (kept {kept})")
                for msg in report["skipped"]:
                    st.write(f"- {msg}")
                for ep, ws in report["warnings"].items():
                    for w in ws:
                        st.write(f"- episode {ep}: {w}")

with right:
    mode = st.radio("Analysis mode", ["Keyword baseline (no key needed)", "AI-powered (your own API key)"],
                    horizontal=False, key="mode")
    ai = mode.startswith("AI")
    base_url = model = api_key = ""
    pause, trailers = 6.0, 3
    if ai:
        provider = st.selectbox("Model provider", list(PRESETS), format_func=lambda x: x.title())
        base_url = provider
        model = st.text_input("Model name", placeholder="Enter a model ID supported by your provider")
        api_key = st.text_input("Your API key", type="password",
                                help="Used only for this browser session. Never saved or included in downloads.")
        c1, c2 = st.columns(2)
        pause = c1.number_input("Seconds between calls", 0.0, 60.0, 6.0, 0.5,
                                help="Raise this if the provider returns rate-limit errors (429).")
        trailers = c2.number_input("Multi-episode trailers", 0, 5, 3)
        st.caption("What is sent to the provider: screenplay dialogue, actions, camera notes and timestamps, plus "
                   "candidate clip text. Check the provider's terms before sending confidential scripts. The public "
                   "app accepts preset hosted providers only; custom endpoints remain available through the local CLI.")
    with st.expander("Advanced settings"):
        d = Settings()
        per_episode = st.slider("Clips shortlisted per episode", 1, 4, d.per_episode)
        strong = st.slider("Minimum line mark to open or close a clip (0-3)", 1, 3, d.strong)
        scene_pen = st.slider("Penalty per scene change inside a clip", 0.0, 3.0, d.scene_change_penalty, 0.5)
        st.caption("Stranger-test isolation is fixed at one promo per model call so clips cannot influence one another.")

    ready = bool(parsed and parsed[0] == "ok")
    blockers = []
    if not ready:
        blockers.append("upload screenplays")
    if ai and not (base_url and model and api_key):
        blockers.append("enter a provider, model name and API key")
    if ai and ready and len(parsed[1][0]) > MAX_AI_EPISODES:
        blockers.append(f"AI mode handles at most {MAX_AI_EPISODES} episodes per run here")
    if ai and st.session_state.get("ai_runs", 0) >= MAX_AI_RUNS_PER_SESSION:
        blockers.append(f"this session has used its {MAX_AI_RUNS_PER_SESSION} AI runs")
    go = st.button("Analyse", type="primary", disabled=bool(blockers), width="stretch")
    if blockers:
        st.caption("To start: " + "; ".join(blockers) + ".")
    st.button("Clear session", on_click=clear_session, help="Forget uploads, results and cached model answers.")

# ============================================================== processing
STAGES = [("read", "Reading input files"), ("parse", "Parsing episodes and extracting screenplay beats"),
          ("tag", "Evaluating hooks and cliffhangers, building candidate promos"),
          ("trailers", "Building multi-episode trailers"), ("judge", "Evaluating standalone clarity (stranger test)"),
          ("audit", "Running quality audits and ranking"), ("outputs", "Generating downloadable outputs")]

if go:
    cfg = Settings(per_episode=per_episode, strong=strong, scene_change_penalty=scene_pen,
                   judge_batch=1, trailers=int(trailers) if ai else 0)
    llm = None
    if ai:
        st.session_state["ai_runs"] = st.session_state.get("ai_runs", 0) + 1
        # Separate cache by input pack and provider/model. This prevents the downloadable
        # raw-output ZIP from mixing prompts from different uploads or model configurations.
        model_key = hashlib.sha256(f"{base_url}|{model.strip()}".encode()).hexdigest()[:12]
        cache = os.path.join(session_dir(), st.session_state["fingerprint"], model_key, "llm_calls")
        llm = LLM(base_url, model.strip(), api_key.strip(), cache_dir=cache, pause=float(pause),
                  log=lambda m: None)
    t0 = time.time()
    with st.status("Analysing...", expanded=True) as status:
        bar = st.progress(0.0, "Starting")
        m1, m2, m3, m4 = st.columns(4)
        cur, done_m, fail_m, time_m = m1.empty(), m2.empty(), m3.empty(), m4.empty()
        stage_box = st.empty()
        state = {"stage": "read", "seen": set()}

        def show_stages():
            lines = []
            for key, label in STAGES:
                if key in ("trailers", "judge") and not ai:
                    lines.append(f"- ~~{label}~~ (skipped in keyword mode)")
                elif key in state["seen"] and key != state["stage"]:
                    lines.append(f"- ✅ {label}")
                elif key == state["stage"]:
                    lines.append(f"- ⏳ **{label}**")
                else:
                    lines.append(f"- {label}")
            stage_box.markdown("\n".join(lines))

        def progress(ev):
            state["seen"].add(state["stage"])
            state["stage"] = ev["stage"]
            state["seen"].add("read")
            done, total = ev.get("done", 0), max(1, ev.get("total", 1))
            if ev["stage"] == "tag":
                bar.progress(done / total, f"Episode {ev['episode']} ({done} of {total})")
                cur.metric("Current episode", ev["episode"])
                done_m.metric("Episodes done", f"{done}/{total}")
                fb = len(ev.get("fallback", []))
                fail_m.metric("Failed calls / fallbacks", f"{ev.get('failed_calls', 0)} / {fb}")
            elif ev["stage"] == "judge":
                bar.progress(done / total, f"Stranger test: clip {done} of {total}")
                done_m.metric("Clips judged", f"{done}/{total}")
                fail_m.metric("Failed calls", llm.stats["failed"] if llm else 0)
            time_m.metric("Elapsed", f"{time.time() - t0:.0f} s")
            show_stages()

        show_stages()
        try:
            result = pipeline.run(parsed[2], llm, cfg, progress=progress)
            state["seen"].add(state["stage"])
            state["stage"] = "outputs"
            show_stages()
            st.session_state["zip"] = pipeline.outputs_zip(result, llm.cache_dir if llm else None)
            st.session_state["result"] = result
            state["seen"].add("outputs")
            state["stage"] = ""
            show_stages()
            bar.progress(1.0, "Done")
            status.update(label=f"Done in {time.time() - t0:.0f} s: {len(result['promos'])} promos",
                          state="complete", expanded=False)
        except (InputError, RuntimeError) as e:
            status.update(label="The run stopped", state="error", expanded=True)
            st.error(str(e))

# ============================================================== results
result = st.session_state.get("result")
if not result:
    st.stop()

promos, log, episodes = result["promos"], result["log"], result["episodes"]
s = log["llm"] or {}
st.header("2. Results")
st.caption(f"Mode: {log['mode']}. Everything below comes from this run.")
if s.get("failed") or log["episodes_tagged_by_keywords_after_ai_failed"]:
    st.warning(f"{s.get('failed', 0)} model call(s) failed after all retries; episodes tagged by keywords instead: "
               f"{log['episodes_tagged_by_keywords_after_ai_failed'] or 'none'}. Click Analyse again to retry only "
               "the failed calls.")
unjudged = [p for p in promos if not p["ai_judged"]]
if log["llm"] and unjudged:
    st.warning(f"{len(unjudged)} promo(s) have no valid AI judgement. Their scores are estimates and are "
               "ranked after the judged promos.")

tab_over, tab_rank, tab_insp, tab_qual, tab_dl = st.tabs(
    ["Overview", "Ranked promos", "Promo inspector", "Quality", "Downloads"])

df = pd.DataFrame([{"Rank": p["rank"], "Score": p["score"], "Type": p["type"],
                    "Episode": ", ".join(str(x["episode"]) for x in p["segments"]),
                    "First episode": p["segments"][0]["episode"],
                    "Segments": pipeline.seg_label(p), "Seconds": p["seconds"],
                    "Hook": p["hook_score"], "Cliffhanger": p["cliffhanger_score"],
                    "Standalone": p["standalone_score"], "Angle": p["angle"],
                    "Scored by": "AI" if p["ai_judged"] else "estimate",
                    "Hook line": p["hook"], "Warnings": len(p["warnings"])} for p in promos])

with tab_over:
    rows_ = [("Episodes processed", log["episodes"]), ("Screenplay beats", f"{log['beats']:,}"),
             ("Legal clips considered", f"{log['legal_clips_considered']:,}"),
             ("Strong candidate clips", f"{log['clips_with_strong_open_and_close']:,}"),
             ("Final promos", log["promos"]), ("Episodes without a promo", len(log["episodes_with_no_promo"])),
             ("AI calls (sent + cached)", s.get("calls", 0) + s.get("cached", 0) if s else 0),
             ("Failed calls", s.get("failed", 0) if s else 0),
             ("Fallback episodes", len(log["episodes_tagged_by_keywords_after_ai_failed"])),
             ("Tokens in / out", f"{s.get('input_tokens', 0):,} / {s.get('output_tokens', 0):,}" if s else "0 / 0"),
             ("Processing time", f"{log['wall_seconds']:.1f} s")]
    for i in range(0, len(rows_), 4):
        cols = st.columns(4)
        for c, (label, value) in zip(cols, rows_[i:i + 4]):
            c.metric(label, value)
    if promos:
        per_ep = pd.DataFrame({"Episode": list(episodes)})
        counts = df.groupby("First episode").size()
        per_ep["Promos"] = per_ep["Episode"].map(counts).fillna(0).astype(int)
        c1, c2 = st.columns(2)
        c1.markdown("**Promos by episode** (zero = no promo found)")
        c1.altair_chart(alt.Chart(per_ep).mark_bar(color="#be185d").encode(
            x=alt.X("Episode:O", axis=alt.Axis(labelAngle=0, labelOverlap=True)), y=alt.Y("Promos:Q", axis=alt.Axis(tickMinStep=1)),
            tooltip=["Episode", "Promos"]).properties(height=230), width="stretch")
        c2.markdown("**Score distribution**")
        c2.altair_chart(alt.Chart(df).mark_bar(color="#6d28d9").encode(
            x=alt.X("Score:Q", bin=alt.Bin(step=5), title="Score (0-100)"), y=alt.Y("count():Q", title="Promos"),
            tooltip=[alt.Tooltip("count():Q", title="Promos")]).properties(height=230), width="stretch")
        c3, c4 = st.columns(2)
        c3.markdown("**Narrative angle**")
        ang = df.groupby("Angle").size().reset_index(name="Promos")
        c3.altair_chart(alt.Chart(ang).mark_bar(color="#0f766e").encode(
            y=alt.Y("Angle:N", sort="-x", title=None), x="Promos:Q", tooltip=["Angle", "Promos"]
        ).properties(height=230), width="stretch")
        c4.markdown("**Quality warnings on kept promos**")
        wc = pd.Series([w.split(":")[0] for p in promos for w in p["warnings"]]).value_counts()
        if len(wc):
            wdf = wc.reset_index()
            wdf.columns = ["Warning", "Promos"]
            c4.altair_chart(alt.Chart(wdf).mark_bar(color="#b45309").encode(
                y=alt.Y("Warning:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)), x="Promos:Q",
                tooltip=["Warning", "Promos"]).properties(height=230), width="stretch")
        else:
            c4.info("No warnings on any kept promo.")
    else:
        st.info("No clip had both a strong opening and a strong ending. See the Quality tab.")

with tab_rank:
    if not promos:
        st.info("No promos in this run.")
    else:
        f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.2, 1.4])
        ep_pick = f1.multiselect("Episode", sorted(df["First episode"].unique()))
        angle_pick = f2.multiselect("Angle", sorted(df["Angle"].unique()))
        lo, hi = float(df["Score"].min()), float(df["Score"].max())
        score_rng = f3.slider("Score", 0.0, 100.0, (max(0.0, lo - 1), min(100.0, hi + 1)))
        text = f4.text_input("Search hook line")
        view = df[(df["Score"] >= score_rng[0]) & (df["Score"] <= score_rng[1])]
        if ep_pick:
            view = view[view["First episode"].isin(ep_pick)]
        if angle_pick:
            view = view[view["Angle"].isin(angle_pick)]
        if text:
            view = view[view["Hook line"].str.contains(text, case=False, regex=False)]
        st.caption(f"{len(view)} of {len(df)} promos. Click a column header to sort.")
        st.dataframe(view.drop(columns=["First episode"]), hide_index=True, width="stretch", height=480,
                     column_config={
                         "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
                         "Seconds": st.column_config.NumberColumn(format="%.1f s"),
                         "Hook line": st.column_config.TextColumn(width="large")})

with tab_insp:
    if not promos:
        st.info("No promos in this run.")
    else:
        pick = st.selectbox("Promo", promos, format_func=lambda p: f"#{p['rank']}  ·  score {p['score']}  ·  "
                            f"{pipeline.seg_label(p)}  ·  {p['hook'][:60]}")
        a, b, c, d = st.columns(4)
        a.metric("Rank", f"#{pick['rank']}")
        b.metric("Score", pick["score"])
        c.metric("Duration", f"{pick['seconds']:.1f} s")
        d.metric("Evaluated by", "AI judge" if pick["ai_judged"] else "Estimate")
        h1, h2, h3 = st.columns(3)
        h1.metric("Hook", f"{pick['hook_score']:.0f}/10")
        h2.metric("Cliffhanger", f"{pick['cliffhanger_score']:.0f}/10")
        h3.metric("Standalone", f"{pick['standalone_score']:.0f}/10")
        st.markdown(f"**Opening hook**<div class='pf-quote'>{html.escape(pick['hook'])}</div>"
                    f"**Cliffhanger**<div class='pf-quote'>{html.escape(pick['cliffhanger'])}</div>",
                    unsafe_allow_html=True)
        st.markdown(f"**Why it works:** {pick['why']}  \n**Why it is #{pick['rank']}:** {pick['post_reason']}  \n"
                    f"**Angle:** {pick['angle']}  \n**Scored by:** {pick['scored_by']}")
        if pick["retell"]:
            st.markdown(f"**What a stranger thinks is happening:** {pick['retell']}")
        if pick["confusion"]:
            st.markdown(f"**What a stranger did not get:** {pick['confusion']}")
        if pick["warnings"]:
            st.warning("Quality warnings: " + "; ".join(pick["warnings"]))

        st.subheader("Segments and timeline")
        seg_df = pd.DataFrame([{"Segment": i, "Episode": x["episode"], "Start": x["start_ts"], "End": x["end_ts"],
                                "Seconds": x["seconds"]} for i, x in enumerate(pick["segments"], 1)])
        st.dataframe(seg_df, hide_index=True, width="stretch")
        tl = []
        for i, x in enumerate(pick["segments"], 1):
            label = f"Seg {i} · ep {x['episode']}"
            tl.append({"Row": label, "From": 0.0, "To": episodes[x["episode"]]["duration"], "Part": "episode"})
            tl.append({"Row": label, "From": x["start"], "To": x["end"], "Part": "promo"})
        st.altair_chart(alt.Chart(pd.DataFrame(tl)).mark_bar(cornerRadius=4, height=18).encode(
            x=alt.X("From:Q", title="Seconds into the episode"), x2="To:Q",
            y=alt.Y("Row:N", title=None, axis=alt.Axis(labelLimit=200, labelFontSize=12, labelPadding=8)),
            color=alt.Color("Part:N", scale=alt.Scale(domain=["episode", "promo"], range=["#e5e7eb", "#be185d"]),
                            legend=None),
            tooltip=["Row", "Part", alt.Tooltip("From:Q", format=".2f"), alt.Tooltip("To:Q", format=".2f")]
        ).properties(height=80 + 40 * len(pick["segments"])), width="stretch")

        st.subheader("Script inside the clip")
        st.caption("Script-derived only: this is what the screenplay says happens, not the video. "
                   "Times are counted from the start of the promo. Pink = the hook (first 3 s), "
                   "amber = the chosen cliffhanger line.")
        rows_t, offset = [], 0.0
        last_seg = pick["segments"][-1]
        for i, x in enumerate(pick["segments"]):
            ep = episodes[x["episode"]]
            for bt in rules.segment_beats(ep, x):
                rel = bt.start - x["start"] + offset
                tag = ""
                if i == 0 and (bt.id == x.get("first_id") or (not x.get("first_id") and rel <= 3 and bt.kind in ("SAY", "SEE"))):
                    tag = "hook"
                if x is last_seg and bt.id == x.get("last_id"):
                    tag = "cliffhanger"
                rows_t.append({"Time": f"{rel:5.1f}s", "Type": {"CAM": "Camera", "SEE": "Action", "SAY": "Dialogue",
                                                               "SFX": "Sound"}[bt.kind],
                               "Character": bt.speaker.split(" (")[0].title() if bt.speaker else "",
                               "Text": bt.text, "_tag": tag})
            offset += x["seconds"]
        tdf = pd.DataFrame(rows_t)
        colors = {"hook": "background-color: rgba(190, 24, 93, 0.18)",
                  "cliffhanger": "background-color: rgba(217, 119, 6, 0.22)"}
        styled = tdf.drop(columns=["_tag"]).style.apply(
            lambda r: [colors.get(tdf.loc[r.name, "_tag"], "")] * len(r), axis=1)
        st.dataframe(styled, hide_index=True, width="stretch", height=420,
                     column_config={"Text": st.column_config.TextColumn(width="large")})

with tab_qual:
    n = len(promos)
    rej = [r for r in result["rejected"] if r["stage"] == "audit"]
    a, b, c, d = st.columns(4)
    a.metric("Kept promos passing every mandatory rule", f"{n}/{n}")
    b.metric("Rejected by the audit", len(rej))
    c.metric("Dropped as duplicates", sum(r["stage"] == "duplicate" for r in result["rejected"]))
    d.metric("Trailer proposals rejected", sum(r["stage"] == "trailer" for r in result["rejected"]))
    st.markdown("**Mandatory rules** (a promo failing any of them is never ranked): 20-60 s long · the chosen hook "
                "line inside the first 3 s · no spoken line cut · nothing said or done after the cliffhanger · "
                "valid timestamps inside the episode.")
    if rej:
        st.markdown("**Rejected by the audit**")
        st.dataframe(pd.DataFrame([{"Segments": "; ".join(f"ep{x['episode']:02d} {x['start_ts']}-{x['end_ts']}"
                                                         for x in r["proposal"]["segments"]),
                                    "Reason": r["reason"]} for r in rej]), hide_index=True, width="stretch")
    if promos:
        st.markdown("**Weakest promos**")
        st.dataframe(df.sort_values("Score").head(5)[["Rank", "Score", "Segments", "Hook", "Cliffhanger",
                                                      "Standalone", "Scored by"]], hide_index=True, width="stretch")
        low = [p for p in promos if p["ai_judged"] and p["standalone_score"] <= Settings().low_standalone]
        st.markdown(f"**Poor standalone clarity** (stranger's mark 4 or less): {len(low)}")
        if low:
            st.dataframe(pd.DataFrame([{"Rank": p["rank"], "Segments": pipeline.seg_label(p),
                                        "Standalone": p["standalone_score"], "What confused the stranger": p["confusion"]}
                                       for p in low]), hide_index=True, width="stretch")
        notes = [p["confusion"] for p in promos if p["confusion"]]
        if notes:
            with st.expander(f"Every confusion note from the stranger test ({len(notes)})"):
                for x in notes:
                    st.write(f"- {x}")
    st.markdown(f"**Episodes with no promo** ({len(log['episodes_with_no_promo'])}): "
                f"{', '.join(map(str, log['episodes_with_no_promo'])) or 'none'}")
    if s:
        st.markdown(f"**Model calls:** {s['calls']} sent, {s['cached']} from cache, {s['failed']} failed after retries, "
                    f"{s.get('retries', 0)} retries, {s['bad_json']} invalid JSON answers.")
        if s.get("errors"):
            with st.expander("Recent model-call problems"):
                for e in s["errors"]:
                    st.write(f"- {e}")
    with st.expander("Full quality report"):
        st.markdown(pipeline.quality_report(result))

with tab_dl:
    st.markdown("Every file below is generated from this run. The API key is never included.")
    c1, c2, c3, c4 = st.columns(4)
    c1.download_button("Ranked promos (CSV)", pipeline.to_csv(promos), "promos_ranked.csv", "text/csv", width="stretch")
    c2.download_button("Ranked promos (JSON)", json.dumps(promos, ensure_ascii=False, indent=1),
                       "promos_ranked.json", "application/json", width="stretch")
    c3.download_button("Quality report (MD)", pipeline.quality_report(result), "quality_report.md",
                       "text/markdown", width="stretch")
    c4.download_button("All raw outputs (ZIP)", st.session_state.get("zip", b""), "promo_finder_outputs.zip",
                       "application/zip", width="stretch")
    st.caption("The ZIP holds promos_ranked.csv/json, quality_report.md, line_tags.json, rejected.json, "
               "run_log.json and, in AI mode, every prompt and raw model answer (llm_calls/).")
