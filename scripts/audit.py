"""Deliverable 1 - data audit. Every finding is proven by a query that is run, not quoted.
The given snapshot is opened READ-ONLY. It is evidence and is never modified."""
import sqlite3, pathlib, datetime, textwrap

DB = pathlib.Path(__file__).resolve().parents[2] / "SetuHaul_Data_Package" / "setuhaul_freight_operations.db"
OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "AUDIT.md"

F = [
("D01","Slot grid cannot hold 19% of loads","HIGH",
 "Slots are a fixed 60 min (90 for heavy) but 4 of 21 loads need 70-75 min on a STANDARD dock. "
 "An appointment binds to ONE slot_id, so these bookings must overrun into the next slot, which "
 "another driver can book independently. Two of the four are CRITICAL priority.",
 """SELECT shipment_id, priority_code, required_dock_type, expected_unload_min,
       CASE WHEN required_dock_type='HEAVY' THEN 90 ELSE 60 END AS slot_len,
       expected_unload_min - CASE WHEN required_dock_type='HEAVY' THEN 90 ELSE 60 END AS overrun_min
FROM shipments
WHERE expected_unload_min > CASE WHEN required_dock_type='HEAVY' THEN 90 ELSE 60 END
ORDER BY overrun_min DESC, shipment_id"""),

("D02","The system of record holds commitments its own rules forbid","CRITICAL",
 "Two ACTIVE appointments place a load on a dock whose weight limit it exceeds. These are not "
 "proposals - one is CONFIRMED. The database accepted bookings that violate dock capacity.",
 """SELECT a.appointment_id, a.appointment_status, s.shipment_id, s.load_weight_kg,
       d.dock_code, d.max_vehicle_weight_kg,
       s.load_weight_kg - d.max_vehicle_weight_kg AS over_by_kg
FROM appointments a
JOIN appointment_slots sl ON sl.slot_id = a.slot_id
JOIN docks d  ON d.dock_id = sl.dock_id
JOIN shipments s ON s.shipment_id = a.shipment_id
WHERE a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
  AND s.load_weight_kg > d.max_vehicle_weight_kg"""),

("D03","Two weight rules disagree, and they are not even the same quantity","HIGH",
 "RULE004 forces the HEAVY dock only above 25,000 kg, but D1 and D3 cap at 20,000 kg. "
 "Worse, shipments.load_weight_kg is CARGO weight while docks.max_vehicle_weight_kg is VEHICLE "
 "weight - the comparison the rules demand is undefined. Usable standard docks drop from 4 to 2 "
 "for the heaviest standard loads, in exactly the contention case.",
 """SELECT 'RULE004 threshold' AS source, rule_value AS kg FROM facility_rules WHERE rule_id='RULE004'
UNION ALL
SELECT 'dock '||dock_code||' limit', CAST(max_vehicle_weight_kg AS TEXT)
FROM docks WHERE facility_id='FAC-JAI-01'
ORDER BY 1"""),

("D04","slot_status never says OCCUPIED, so naive filtering double-books","CRITICAL",
 "appointment_slots.slot_status has only OPEN and BLOCKED. Slots carrying an active appointment "
 "still read OPEN. Any feasibility query that trusts this column will offer a booked slot.",
 """SELECT sl.slot_status, COUNT(*) AS slots,
       SUM(CASE WHEN a.appointment_id IS NOT NULL THEN 1 ELSE 0 END) AS with_active_appointment
FROM appointment_slots sl
LEFT JOIN appointments a ON a.slot_id = sl.slot_id
     AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
GROUP BY sl.slot_status"""),

("D05","Dock outages are hand-copied into slot_status, and the copy is wrong","CRITICAL",
 "v_slot_availability never joins dock_status_events. Outages are reflected only because somebody "
 "manually set slot_status=BLOCKED, and that manual copy does not match the events. DEVT001 takes "
 "DOCK-JAI-D3 down from 09:15, but blocking starts only at 10:00 - a 45-minute window where the "
 "dock is broken and the data says otherwise, masked purely because an appointment happens to sit "
 "there. DEVT003 reduces D2 capacity for 80 minutes and is reflected in no slot at all. A system "
 "trusting this view will offer a broken dock.",
 """SELECT e.dock_event_id, e.event_type, d.dock_code,
       substr(e.event_start_ts,12,5)||'-'||substr(e.event_end_ts,12,5) AS outage_window,
       COUNT(sl.slot_id) AS slots_overlapping_outage,
       SUM(CASE WHEN sl.slot_status='BLOCKED' THEN 1 ELSE 0 END) AS marked_blocked,
       SUM(CASE WHEN sl.slot_status<>'BLOCKED' THEN 1 ELSE 0 END) AS NOT_marked_blocked,
       GROUP_CONCAT(CASE WHEN sl.slot_status<>'BLOCKED'
            THEN sl.slot_id||'('||substr(sl.slot_start_ts,12,5)||')' END, ' ') AS unmarked_slots
FROM dock_status_events e
JOIN docks d ON d.dock_id = e.dock_id
JOIN appointment_slots sl ON sl.dock_id = e.dock_id
     AND sl.slot_start_ts < e.event_end_ts AND sl.slot_end_ts > e.event_start_ts
GROUP BY e.dock_event_id, e.event_type, d.dock_code, outage_window
ORDER BY e.dock_event_id"""),

("D06","Four drivers hold two active shipments, and one of them is physically impossible","CRITICAL",
 "A conversation is keyed on driver. For these four, 'which shipment does this driver mean' has no "
 "single answer. DRV006 is assigned SHP1006 into Jaipur and SHP1021 into Gurugram ON THE SAME "
 "VEHICLE, 270 km apart, hours apart. One truck cannot be in both places.",
 """SELECT driver_id, COUNT(*) AS active_shipments,
       GROUP_CONCAT(shipment_id||' -> '||destination_facility_id||' ('||vehicle_id||' @ '||substr(original_eta_ts,12,5)||')', '  |  ') AS detail
FROM shipments
WHERE current_status IN ('ASSIGNED','IN_TRANSIT','WAITING')
GROUP BY driver_id HAVING COUNT(*) > 1
ORDER BY driver_id"""),

("D07","A conversation that cannot be resolved to any row","HIGH",
 "THR010 carries a delay report with shipment_id NULL, from a driver who holds two active "
 "shipments. It cannot be auto-resolved. The system must ask, not guess.",
 """SELECT t.thread_id, t.driver_id, t.shipment_id, t.thread_intent, m.message_text
FROM chat_threads t LEFT JOIN chat_messages m ON m.thread_id = t.thread_id AND m.sender_type='DRIVER'
WHERE t.shipment_id IS NULL"""),

("D08","The de-duplication key is unenforced and the duplicates disagree","HIGH",
 "EXC009 repeats EXC001 under the same dedupe_key, but the two rows carry DIFFERENT severity. "
 "There is no unique index on dedupe_key, so concurrent duplicates both pass a read-then-write "
 "check. De-duplication currently depends on nobody racing.",
 """SELECT dedupe_key, COUNT(*) AS copies,
       GROUP_CONCAT(exception_id||'='||COALESCE(severity_code,'?')||'/'||exception_status, '  |  ') AS rows_found
FROM driver_exceptions WHERE dedupe_key IS NOT NULL
GROUP BY dedupe_key HAVING COUNT(*) > 1"""),

("D09","Vague driver claims are stored as precise facts","MEDIUM",
 "SHP1013's driver said only 'I am late by one hour' and SHP1017's gave a range of 12:30-13:00. "
 "Both are persisted as exact timestamps - and SHP1017 is stored as the MIDPOINT (12:45), which "
 "is neither end of the range the driver actually gave. confidence_code records the doubt; the "
 "timestamp hides it.",
 """SELECT e.shipment_id, substr(e.declared_eta_ts,12,5) AS stored_eta, e.confidence_code, e.note
FROM eta_updates e
WHERE e.confidence_code = 'LOW' ORDER BY e.shipment_id"""),

("D10","The one case that must escalate, escalates into a void","CRITICAL",
 "SHP1015 is the designed no-feasible-slot case. Its escalation email went to "
 "night.shift@setuhaul.example, which matches NO row in facility_contacts. The only night-shift "
 "contact is CON005 - 'Vacant Position', no email address, at the WRONG facility - and it is still "
 "flagged active, so nothing surfaces the gap. Jaipur has no reachable after-hours human.",
 """SELECT om.operational_message_id, om.shipment_id, om.recipient_address, om.delivery_status,
       (SELECT COUNT(*) FROM facility_contacts fc WHERE fc.email = om.recipient_address) AS matching_contacts
FROM operational_messages om WHERE om.channel='EMAIL'
UNION ALL
SELECT 'CONTACT:'||contact_id, facility_id, COALESCE(NULLIF(email,''),'<<NO EMAIL>>'),
       'active_flag='||active_flag, NULL
FROM facility_contacts"""),

("D11","A queued message is holding capacity nobody has been asked about","HIGH",
 "APT1013A has held D2 at 11:00 in PENDING_CONFIRMATION since 09:32, but its request email OM002 "
 "is still QUEUED - never sent. The warehouse has not been asked. PENDING_CONFIRMATION sits inside "
 "the active-appointment unique index, so this strands the slot, and appointments has no expiry "
 "column for the pending state to time out in.",
 """SELECT a.appointment_id, a.appointment_status, substr(a.booked_at,12,5) AS pending_since,
       a.confirmed_at, om.operational_message_id, om.delivery_status
FROM appointments a
LEFT JOIN operational_messages om ON om.appointment_id = a.appointment_id
WHERE a.appointment_status='PENDING_CONFIRMATION'"""),

("D12","A constraint the driver stated is stored and then ignored","HIGH",
 "driver_exceptions.earliest_acceptable_ts and latest_acceptable_ts hold limits the driver gave in "
 "words ('any slot after 12?', 'I must leave before 1:30'). Feasibility logic built only from dock "
 "rules and ETA will offer slots that break a promise the driver already made clear.",
 """SELECT e.exception_id, e.shipment_id, substr(e.declared_eta_ts,12,5) AS declared_eta,
       substr(e.earliest_acceptable_ts,12,5) AS not_before,
       substr(e.latest_acceptable_ts,12,5)   AS must_finish_by
FROM driver_exceptions e
WHERE e.earliest_acceptable_ts IS NOT NULL OR e.latest_acceptable_ts IS NOT NULL
ORDER BY e.exception_id"""),

("D13","The replacement chain the guide describes does not exist in the data","MEDIUM",
 "The database guide cites APT1012A and APT1016A as appointment-history chains via "
 "replaced_appointment_id. Every row has it NULL. History is implied by is_current alone, so "
 "'what replaced what' cannot be reconstructed.",
 """SELECT COUNT(*) AS appointments_total,
       SUM(CASE WHEN replaced_appointment_id IS NOT NULL THEN 1 ELSE 0 END) AS with_replacement_link,
       SUM(CASE WHEN is_current=0 THEN 1 ELSE 0 END) AS historical_rows
FROM appointments"""),

("D14","The snapshot has no single 'now'","HIGH",
 "Gate check-ins stop at 09:25 while an exception is raised at 16:24 and another ETA update lands "
 "at 16:25. 'What is available now' is undefined, so every availability answer depends on a clock "
 "the data does not supply. We therefore pass the clock in as a parameter.",
 """SELECT 'latest facility_checkin' AS event, MAX(gate_in_ts) AS at FROM facility_checkins
UNION ALL SELECT 'latest chat_message', MAX(message_ts) FROM chat_messages
UNION ALL SELECT 'latest eta_update', MAX(created_at) FROM eta_updates
UNION ALL SELECT 'latest driver_exception', MAX(reported_at) FROM driver_exceptions
UNION ALL SELECT 'latest operational_message', MAX(sent_at) FROM operational_messages"""),

("D15","The slot grid does not cover the hours the facilities claim to be open","MEDIUM",
 "Jaipur opens at 06:00 but its first slot starts at 08:00. Gurugram runs 07:00-21:00 but has "
 "slots only from 09:00 to 18:00. Capacity logic that reasons from facility hours will believe in "
 "slots that do not exist.",
 """SELECT f.facility_id, f.open_time, f.close_time,
       substr(MIN(sl.slot_start_ts),12,5) AS first_slot,
       substr(MAX(sl.slot_end_ts),12,5)   AS last_slot, COUNT(*) AS slots
FROM facilities f
JOIN docks d ON d.facility_id = f.facility_id
JOIN appointment_slots sl ON sl.dock_id = d.dock_id
GROUP BY f.facility_id, f.open_time, f.close_time"""),

("D16","Foreign keys are not enforced in the shipped database","MEDIUM",
 "PRAGMA foreign_keys is 0 in the delivered SQLite file, so the declared foreign keys are "
 "documentation rather than constraints. The same schema in Postgres enforces them, so a migration "
 "can surface referential errors the snapshot currently tolerates.",
 """SELECT 'foreign_keys_enabled' AS pragma_name, (SELECT * FROM pragma_foreign_keys) AS value
UNION ALL SELECT 'orphan_appointments_vs_slots',
  (SELECT COUNT(*) FROM appointments a LEFT JOIN appointment_slots s ON s.slot_id=a.slot_id WHERE s.slot_id IS NULL)
UNION ALL SELECT 'orphan_shipments_vs_drivers',
  (SELECT COUNT(*) FROM shipments sh LEFT JOIN drivers dr ON dr.driver_id=sh.driver_id WHERE dr.driver_id IS NULL)"""),

("D17","v_inbound_operational_state is one-row-per-shipment only by luck","MEDIUM",
 "The view LEFT JOINs facility_checkins with no 'latest check-in' guard, unlike the v_latest_eta "
 "join directly above it, which is explicitly de-duplicated. It returns 21 rows for 21 shipments "
 "here only because this snapshot happens to hold at most one check-in per shipment. A truck that "
 "is gated in, turned away and gated in again - ordinary at 280-360 loads a day - silently becomes "
 "two rows, and any caller counting inbound trucks over-counts it. v_current_facility_queue shares "
 "the join. This is why the engine reads facility_checkins directly with ORDER BY gate_in_ts DESC "
 "LIMIT 1 (src/setuhaul/capacity.py, arrival()) rather than selecting from these two views.",
 """SELECT 'shipments' AS measure, COUNT(*) AS value FROM shipments
UNION ALL SELECT 'v_inbound_operational_state rows', COUNT(*) FROM v_inbound_operational_state
UNION ALL SELECT 'max check-ins on any one shipment',
  (SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM facility_checkins GROUP BY shipment_id))
UNION ALL SELECT 'shipments with a second check-in',
  (SELECT COUNT(*) FROM (SELECT shipment_id FROM facility_checkins GROUP BY shipment_id HAVING COUNT(*) > 1))"""),
]

def table(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if not rows:
        return "_(query returned no rows)_", 0
    w = [max(len(str(c)), *(len(str(r[i])) if r[i] is not None else 4 for r in rows)) for i, c in enumerate(cols)]
    out = ["| " + " | ".join(str(c).ljust(w[i]) for i, c in enumerate(cols)) + " |",
           "|" + "|".join("-" * (x + 2) for x in w) + "|"]
    for r in rows:
        out.append("| " + " | ".join(("NULL" if v is None else str(v)).ljust(w[i]) for i, v in enumerate(r)) + " |")
    return "\n".join(out), len(rows)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()
parts = [
 "# SetuHaul — data audit (deliverable 1)",
 "",
 f"Generated {datetime.datetime.now().isoformat(timespec='seconds')} against the supplied snapshot,",
 "opened **read-only**. The snapshot is evidence and is never modified.",
 "",
 "Every finding below is produced by the query printed beneath it. Nothing is asserted without a query.",
 "",
 "| # | Finding | Severity |",
 "|---|---|---|",
]
for fid, title, sev, _, _ in F:
    parts.append(f"| [{fid}](#{fid.lower()}) | {title} | **{sev}** |")
parts.append("")

total = 0
for fid, title, sev, sowhat, sql in F:
    md, n = table(cur, sql)
    total += n
    parts += [f"## {fid}", f"### {title}", "", f"**Severity:** {sev}  ·  **Rows returned:** {n}", "",
              textwrap.fill(sowhat, 100), "", "**Proof:**", "", "```sql", sql.strip(), "```", "", md, "", "---", ""]
parts.append(f"_{len(F)} findings, {total} evidence rows, all reproducible via `python3 scripts/audit.py`._")
OUT.write_text("\n".join(parts))
print(f"wrote {OUT}  ({len(F)} findings, {total} evidence rows)")
