"""Greenhouse Data Health dashboard.

Answers, at a glance: did the last run work, can I trust the data, and where are the problems?
Run with:  make dashboard   (then open http://localhost:8501)
"""
from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st

import queries as q

st.set_page_config(page_title="Greenhouse Data Health", page_icon="🌱", layout="wide")

# Colours: validated categorical palette (identity) + reserved status colours (state).
# Status colours are never used alone: every one ships with an icon and a text label.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]   # categorical slots 1-4, fixed order
GOOD, CRITICAL, MUTED = "#0ca30c", "#d03b3b", "#8a8984"
CROP_ORDER = ["Bell Pepper", "Mini Cucumber", "Mini Sweet Pepper", "Tomato on the Vine"]
SOURCE_LABELS = {"climate": "Climate sensors", "erp_harvest": "ERP harvest",
                 "wms_shipments": "WMS shipments", "cross_system": "Cross-system"}
RUN_STATUS = {"succeeded": "✅ Succeeded", "succeeded_with_warnings": "⚠️ Succeeded with warnings",
              "failed": "❌ Failed", "running": "⏳ Running"}
REC_TOLERANCE = 1.20


@st.cache_data(ttl=60, show_spinner=False)
def load(name: str, *params) -> pd.DataFrame:
    return getattr(q, name)(*params)


def status_label(s: str) -> str:
    return "✅ Pass" if s == "Pass" else "❌ Fail"


# ------------------------------------------------------------------ header
head_l, head_r = st.columns([5, 1])
head_l.title("🌱 Greenhouse Data Health")
if head_r.button("↻ Refresh", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

runs = load("runs")
if runs.empty:
    st.info("No pipeline runs yet. Run `make run`, then refresh.")
    st.stop()

rules = load("latest_rules")
qsum = load("quarantine_summary")
counts = load("warehouse_counts")
last = runs.iloc[0]
head_l.caption(f"Last run #{last.run_id} · {RUN_STATUS.get(last.status, last.status)} · "
               f"started {pd.Timestamp(last.started_at):%Y-%m-%d %H:%M} UTC · data refreshes every 60 s")

# ------------------------------------------------------------------ KPI row
passed = int((rules.status == "Pass").sum())
missing_days = int(rules.loc[rules.rule_id.isin(["CLM-101", "SHP-101"]), "rows_failed"].sum())
open_rows = int(qsum.rows_open.sum()) if not qsum.empty else 0
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Last run", RUN_STATUS.get(last.status, last.status).split(" ", 1)[-1],
          help=f"Run #{last.run_id}: {last.message or ''}")
k2.metric("Rules passing", f"{passed} / {len(rules)}", help="Latest result of each active DQ rule")
k3.metric("Quarantined rows (open)", f"{open_rows:,}", help="Row-rule failures waiting for triage")
k4.metric("Missing daily files", missing_days, help="Days with no file from climate or shipments (CLM-101, SHP-101)")
k5.metric("Rows in warehouse", f"{int(counts.n.sum()):,}",
          help=" · ".join(f"{r.item}: {int(r.n):,}" for r in counts.itertuples()))

tab_dq, tab_files, tab_rec, tab_q, tab_ops = st.tabs(
    ["Data quality", "Files & runs", "Reconciliation", "Quarantine", "Operations"])

# ------------------------------------------------------------------ data quality
with tab_dq:
    st.subheader("Rules passing by source")
    cols = st.columns(len(SOURCE_LABELS))
    for col, (src, label) in zip(cols, SOURCE_LABELS.items()):
        sub = rules[rules.source_system == src]
        if not sub.empty:
            ok = int((sub.status == "Pass").sum())
            col.metric(label, f"{ok} / {len(sub)}",
                       delta=f"{len(sub) - ok} failing" if ok < len(sub) else "all passing",
                       delta_color="inverse" if ok < len(sub) else "off")

    failing = rules[rules.status == "Fail"].copy()
    if not failing.empty:
        st.subheader("What failed, and how much")
        failing["label"] = failing.rule_id + "  " + failing.rule_name
        bars = alt.Chart(failing).mark_bar(color=SERIES[0], cornerRadiusEnd=4, height=14).encode(
            x=alt.X("rows_failed:Q", title="Failures (rows, days or crops: see rule)"),
            y=alt.Y("label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=320)),
            tooltip=[alt.Tooltip("rule_id:N", title="Rule"), alt.Tooltip("rule_name:N", title="Name"),
                     alt.Tooltip("severity:N"), alt.Tooltip("on_fail:N", title="Action"),
                     alt.Tooltip("rows_failed:Q", title="Failed"), alt.Tooltip("rows_checked:Q", title="Checked"),
                     alt.Tooltip("details:N")],
        )
        labels = bars.mark_text(align="left", dx=4).encode(text="rows_failed:Q")
        st.altair_chart((bars + labels).properties(height=max(160, 26 * len(failing))), use_container_width=True)

    st.subheader("Rule scorecard")
    card = rules.assign(
        Status=rules.status.map(status_label),
        Source=rules.source_system.map(SOURCE_LABELS),
        failure_pct=rules.failure_rate * 100,
    )[["Status", "rule_id", "rule_name", "Source", "dq_dimension", "severity", "on_fail",
       "rows_checked", "rows_failed", "failure_pct", "details"]]
    st.dataframe(card, hide_index=True, use_container_width=True, column_config={
        "rule_id": "Rule", "rule_name": "Name", "dq_dimension": "Dimension", "severity": "Severity",
        "on_fail": "Action", "rows_checked": st.column_config.NumberColumn("Checked", format="%d"),
        "rows_failed": st.column_config.NumberColumn("Failed", format="%d"),
        "failure_pct": st.column_config.NumberColumn("Failure rate", format="%.2f%%"),
        "details": st.column_config.TextColumn("Details", width="large"),
    })

    history = load("rule_history")
    if len(history) > 1:
        st.subheader("Failing rules per run")
        line = alt.Chart(history).mark_line(color=SERIES[0], strokeWidth=2, point=alt.OverlayMarkDef(size=64)).encode(
            x=alt.X("run_id:O", title="Run"),
            y=alt.Y("rules_failed:Q", title="Rules failing"),
            tooltip=[alt.Tooltip("run_id:O", title="Run"), alt.Tooltip("rules_failed:Q", title="Rules failing"),
                     alt.Tooltip("rules_checked:Q", title="Rules checked")],
        )
        st.altair_chart(line.properties(height=220), use_container_width=True)

# ------------------------------------------------------------------ files & runs
with tab_files:
    files = load("files")
    st.subheader("Daily file arrivals")
    st.caption("One file is expected per day from each daily feed. The ERP harvest export is monthly and not shown.")
    daily = files[(files.status == "loaded") & files.source_system.isin(["climate", "wms_shipments"])].copy()
    if not daily.empty:
        daily["file_date"] = pd.to_datetime(daily.file_date)
        grid = []
        for src, grp in daily.groupby("source_system"):
            got = set(grp.file_date)
            for d in pd.date_range(grp.file_date.min(), grp.file_date.max()):
                grid.append({"source": SOURCE_LABELS[src], "date": d,
                             "arrival": "Delivered" if d in got else "Missing"})
        grid = pd.DataFrame(grid)
        base = alt.Chart(grid).encode(
            x=alt.X("monthdate(date):O", title=None, axis=alt.Axis(labelAngle=-90)),
            y=alt.Y("source:N", title=None),
        )
        cells = base.mark_rect(cornerRadius=3).encode(
            color=alt.Color("arrival:N", scale=alt.Scale(domain=["Delivered", "Missing"], range=[GOOD, CRITICAL]),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=[alt.Tooltip("source:N"), alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("arrival:N")],
        )
        marks = base.transform_filter(alt.datum.arrival == "Missing").mark_text(
            text="✕", color="white", fontWeight="bold", fontSize=13)
        st.altair_chart((cells + marks).properties(height=130).configure_scale(bandPaddingInner=0.12),
                        use_container_width=True)

    st.subheader("File-level reconciliation")
    st.caption("Every file's row count in the source must equal the rows landed in staging.")
    f = files.assign(
        Check=lambda d: d.apply(lambda r: "✅ Match" if r.rows_in_file == r.rows_loaded else "❌ Mismatch", axis=1),
        Source=files.source_system.map(SOURCE_LABELS),
    )[["Check", "Source", "file_name", "rows_in_file", "rows_loaded", "status", "processed_run_id"]]
    st.dataframe(f, hide_index=True, use_container_width=True, height=280, column_config={
        "file_name": "File", "rows_in_file": "Rows in file", "rows_loaded": "Rows loaded",
        "status": "Load status", "processed_run_id": st.column_config.NumberColumn("Processed in run", format="%d"),
    })

    st.subheader("Pipeline runs")
    r = runs.assign(Status=runs.status.map(lambda s: RUN_STATUS.get(s, s)))
    st.dataframe(r[["run_id", "Status", "started_at", "duration_s", "message"]], hide_index=True,
                 use_container_width=True, column_config={
                     "run_id": "Run", "started_at": st.column_config.DatetimeColumn("Started (UTC)"),
                     "duration_s": st.column_config.NumberColumn("Duration (s)", format="%d"),
                     "message": "Message"})

# ------------------------------------------------------------------ reconciliation
with tab_rec:
    rec = load("reconciliation")
    st.subheader("Shipped vs harvested, by crop")
    st.caption(f"Grade A+B harvest is what can be sold. Shipping more than {REC_TOLERANCE:.0%} of it "
               "is physically impossible and fails rule REC-001 (the gap allows for opening stock).")
    if not rec.empty:
        rec = rec.assign(
            check=lambda d: d.shipped_to_harvest_ratio.map(
                lambda x: "Over tolerance: REC-001 fails" if pd.notna(x) and x > REC_TOLERANCE else "Within tolerance"),
            ratio_label=lambda d: d.shipped_to_harvest_ratio.map(lambda x: f"{x:.2f}×" if pd.notna(x) else "n/a"),
        )
        ratio = alt.Chart(rec).mark_bar(cornerRadiusEnd=4, height=22).encode(
            x=alt.X("shipped_to_harvest_ratio:Q", title="Shipped ÷ sellable harvest",
                    scale=alt.Scale(domain=[0, max(1.6, float(rec.shipped_to_harvest_ratio.max() or 0) + 0.1)])),
            y=alt.Y("crop_name:N", title=None, sort=CROP_ORDER),
            color=alt.Color("check:N", scale=alt.Scale(domain=["Within tolerance", "Over tolerance: REC-001 fails"],
                                                       range=[SERIES[0], CRITICAL]),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=[alt.Tooltip("crop_name:N", title="Crop"), alt.Tooltip("ratio_label:N", title="Ratio"),
                     alt.Tooltip("sellable_kg:Q", title="Sellable kg", format=",.0f"),
                     alt.Tooltip("shipped_kg:Q", title="Shipped kg", format=",.0f"), alt.Tooltip("check:N")],
        )
        ratio_text = alt.Chart(rec).mark_text(align="left", dx=4).encode(   # neutral ink, not the bar colour
            x="shipped_to_harvest_ratio:Q", y=alt.Y("crop_name:N", sort=CROP_ORDER), text="ratio_label:N")
        refs = pd.DataFrame({"x": [1.0, REC_TOLERANCE], "label": ["1.0 = balanced", f"{REC_TOLERANCE:.1f} limit"]})
        ref_lines = alt.Chart(refs).mark_rule(strokeDash=[4, 4], color=MUTED).encode(x="x:Q")
        ref_text = alt.Chart(refs).mark_text(align="left", dx=3, dy=-6, color=MUTED, baseline="bottom").encode(
            x="x:Q", y=alt.value(0), text="label:N")
        st.altair_chart((ratio + ratio_text + ref_lines + ref_text).properties(height=200),
                        use_container_width=True)

        kg = rec.melt(id_vars="crop_name", value_vars=["sellable_kg", "shipped_kg"], var_name="measure", value_name="kg")
        kg["measure"] = kg.measure.map({"sellable_kg": "Sellable harvest (A+B)", "shipped_kg": "Shipped"})
        grouped = alt.Chart(kg).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("crop_name:N", title=None, sort=CROP_ORDER, axis=alt.Axis(labelAngle=0)),
            xOffset=alt.XOffset("measure:N", sort=["Sellable harvest (A+B)", "Shipped"],
                                scale=alt.Scale(paddingInner=0.04)),
            y=alt.Y("kg:Q", title="kg in period", axis=alt.Axis(format=",.0f")),
            color=alt.Color("measure:N", scale=alt.Scale(domain=["Sellable harvest (A+B)", "Shipped"],
                                                         range=SERIES[:2]),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=[alt.Tooltip("crop_name:N", title="Crop"), alt.Tooltip("measure:N"),
                     alt.Tooltip("kg:Q", format=",.0f")],
        )
        st.altair_chart(grouped.properties(height=260), use_container_width=True)
        with st.expander("Table view"):
            st.dataframe(rec[["crop_name", "sellable_kg", "shipped_kg", "shipped_to_harvest_ratio", "check"]],
                         hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ quarantine
with tab_q:
    st.subheader("Quarantined rows by rule and file")
    st.caption("Rows a rule kept out of the warehouse. Nothing is dropped silently: each row is stored with "
               "the rule that caught it, ready for triage.")
    if qsum.empty:
        st.success("Nothing in quarantine.")
    else:
        by_rule = qsum.groupby(["rule_id", "rule_name"], as_index=False)[["rows_quarantined", "rows_open"]].sum()
        st.dataframe(by_rule, hide_index=True, use_container_width=True, column_config={
            "rule_id": "Rule", "rule_name": "Name",
            "rows_quarantined": st.column_config.NumberColumn("Rows quarantined", format="%d"),
            "rows_open": st.column_config.NumberColumn("Open", format="%d")})

        choice = st.selectbox("Inspect the rows held by a rule",
                              by_rule.rule_id + " · " + by_rule.rule_name)
        rows = load("quarantine_rows", choice.split(" · ")[0])
        if not rows.empty:
            detail = pd.json_normalize(rows.row_data.map(json.loads))
            detail.insert(0, "quarantine_id", rows.quarantine_id.values)
            st.dataframe(detail, hide_index=True, use_container_width=True)
            st.download_button("Download as CSV", detail.to_csv(index=False).encode(),
                               file_name=f"quarantine_{choice.split(' · ')[0]}.csv", mime="text/csv")

# ------------------------------------------------------------------ operations
with tab_ops:
    zy = load("zone_yield")
    st.subheader("Sellable yield per zone")
    st.caption("Grade A+B kg per m² in the period. Zones growing the same crop should look alike: "
               "a zone far below its peers usually means missing data, not a bad crop.")
    if not zy.empty:
        zy = zy.assign(kg_per_m2=zy.sellable_kg.fillna(0) / zy.area_m2)
        crops = [c for c in CROP_ORDER if c in set(zy.crop_name)] + sorted(set(zy.crop_name) - set(CROP_ORDER))
        bars = alt.Chart(zy).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("zone_id:N", title="Zone", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("kg_per_m2:Q", title="Sellable kg per m²"),
            color=alt.Color("crop_name:N", scale=alt.Scale(domain=crops, range=SERIES[:len(crops)]),
                            legend=alt.Legend(title="Crop", orient="top")),
            tooltip=[alt.Tooltip("zone_id:N", title="Zone"), alt.Tooltip("greenhouse:N"),
                     alt.Tooltip("crop_name:N", title="Crop"), alt.Tooltip("kg_per_m2:Q", title="kg/m²", format=".2f"),
                     alt.Tooltip("sellable_kg:Q", title="Sellable kg", format=",.0f"),
                     alt.Tooltip("records:Q", title="Harvest records")],
        )
        text = alt.Chart(zy).mark_text(dy=-6).encode(   # neutral ink, not the crop colour
            x="zone_id:N", y="kg_per_m2:Q", text=alt.Text("kg_per_m2:Q", format=".2f"))
        st.altair_chart((bars + text).properties(height=300), use_container_width=True)
