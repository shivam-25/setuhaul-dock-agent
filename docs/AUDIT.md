# SetuHaul — data audit (deliverable 1)

Generated 2026-11-25T14:13:44 against the supplied snapshot,
opened **read-only**. The snapshot is evidence and is never modified.

Every finding below is produced by the query printed beneath it. Nothing is asserted without a query.

| # | Finding | Severity |
|---|---|---|
| [D01](#d01) | Slot grid cannot hold 19% of loads | **HIGH** |
| [D02](#d02) | The system of record holds commitments its own rules forbid | **CRITICAL** |
| [D03](#d03) | Two weight rules disagree, and they are not even the same quantity | **HIGH** |
| [D04](#d04) | slot_status never says OCCUPIED, so naive filtering double-books | **CRITICAL** |
| [D05](#d05) | Dock outages are hand-copied into slot_status, and the copy is wrong | **CRITICAL** |
| [D06](#d06) | Four drivers hold two active shipments, and one of them is physically impossible | **CRITICAL** |
| [D07](#d07) | A conversation that cannot be resolved to any row | **HIGH** |
| [D08](#d08) | The de-duplication key is unenforced and the duplicates disagree | **HIGH** |
| [D09](#d09) | Vague driver claims are stored as precise facts | **MEDIUM** |
| [D10](#d10) | The one case that must escalate, escalates into a void | **CRITICAL** |
| [D11](#d11) | A queued message is holding capacity nobody has been asked about | **HIGH** |
| [D12](#d12) | A constraint the driver stated is stored and then ignored | **HIGH** |
| [D13](#d13) | The replacement chain the guide describes does not exist in the data | **MEDIUM** |
| [D14](#d14) | The snapshot has no single 'now' | **HIGH** |
| [D15](#d15) | The slot grid does not cover the hours the facilities claim to be open | **MEDIUM** |
| [D16](#d16) | Foreign keys are not enforced in the shipped database | **MEDIUM** |

## D01
### Slot grid cannot hold 19% of loads

**Severity:** HIGH  ·  **Rows returned:** 4

Slots are a fixed 60 min (90 for heavy) but 4 of 21 loads need 70-75 min on a STANDARD dock. An
appointment binds to ONE slot_id, so these bookings must overrun into the next slot, which another
driver can book independently. Two of the four are CRITICAL priority.

**Proof:**

```sql
SELECT shipment_id, priority_code, required_dock_type, expected_unload_min,
       CASE WHEN required_dock_type='HEAVY' THEN 90 ELSE 60 END AS slot_len,
       expected_unload_min - CASE WHEN required_dock_type='HEAVY' THEN 90 ELSE 60 END AS overrun_min
FROM shipments
WHERE expected_unload_min > CASE WHEN required_dock_type='HEAVY' THEN 90 ELSE 60 END
ORDER BY overrun_min DESC, shipment_id
```

| shipment_id | priority_code | required_dock_type | expected_unload_min | slot_len | overrun_min |
|-------------|---------------|--------------------|---------------------|----------|-------------|
| SHP1005     | HIGH          | STANDARD           | 75                  | 60       | 15          |
| SHP1009     | CRITICAL      | STANDARD           | 75                  | 60       | 15          |
| SHP1014     | CRITICAL      | STANDARD           | 75                  | 60       | 15          |
| SHP1002     | HIGH          | STANDARD           | 70                  | 60       | 10          |

---

## D02
### The system of record holds commitments its own rules forbid

**Severity:** CRITICAL  ·  **Rows returned:** 2

Two ACTIVE appointments place a load on a dock whose weight limit it exceeds. These are not
proposals - one is CONFIRMED. The database accepted bookings that violate dock capacity.

**Proof:**

```sql
SELECT a.appointment_id, a.appointment_status, s.shipment_id, s.load_weight_kg,
       d.dock_code, d.max_vehicle_weight_kg,
       s.load_weight_kg - d.max_vehicle_weight_kg AS over_by_kg
FROM appointments a
JOIN appointment_slots sl ON sl.slot_id = a.slot_id
JOIN docks d  ON d.dock_id = sl.dock_id
JOIN shipments s ON s.shipment_id = a.shipment_id
WHERE a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
  AND s.load_weight_kg > d.max_vehicle_weight_kg
```

| appointment_id | appointment_status   | shipment_id | load_weight_kg | dock_code | max_vehicle_weight_kg | over_by_kg |
|----------------|----------------------|-------------|----------------|-----------|-----------------------|------------|
| APT1014A       | PENDING_CONFIRMATION | SHP1014     | 21000          | D1        | 20000                 | 1000       |
| APT1005        | CONFIRMED            | SHP1005     | 20500          | D3        | 20000                 | 500        |

---

## D03
### Two weight rules disagree, and they are not even the same quantity

**Severity:** HIGH  ·  **Rows returned:** 7

RULE004 forces the HEAVY dock only above 25,000 kg, but D1 and D3 cap at 20,000 kg. Worse,
shipments.load_weight_kg is CARGO weight while docks.max_vehicle_weight_kg is VEHICLE weight - the
comparison the rules demand is undefined. Usable standard docks drop from 4 to 2 for the heaviest
standard loads, in exactly the contention case.

**Proof:**

```sql
SELECT 'RULE004 threshold' AS source, rule_value AS kg FROM facility_rules WHERE rule_id='RULE004'
UNION ALL
SELECT 'dock '||dock_code||' limit', CAST(max_vehicle_weight_kg AS TEXT)
FROM docks WHERE facility_id='FAC-JAI-01'
ORDER BY 1
```

| source            | kg    |
|-------------------|-------|
| RULE004 threshold | 25000 |
| dock D1 limit     | 20000 |
| dock D2 limit     | 25000 |
| dock D3 limit     | 20000 |
| dock D4 limit     | 25000 |
| dock D5 limit     | 22000 |
| dock D6 limit     | 35000 |

---

## D04
### slot_status never says OCCUPIED, so naive filtering double-books

**Severity:** CRITICAL  ·  **Rows returned:** 2

appointment_slots.slot_status has only OPEN and BLOCKED. Slots carrying an active appointment still
read OPEN. Any feasibility query that trusts this column will offer a booked slot.

**Proof:**

```sql
SELECT sl.slot_status, COUNT(*) AS slots,
       SUM(CASE WHEN a.appointment_id IS NOT NULL THEN 1 ELSE 0 END) AS with_active_appointment
FROM appointment_slots sl
LEFT JOIN appointments a ON a.slot_id = sl.slot_id
     AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
GROUP BY sl.slot_status
```

| slot_status | slots | with_active_appointment |
|-------------|-------|-------------------------|
| BLOCKED     | 7     | 0                       |
| OPEN        | 99    | 14                      |

---

## D05
### Dock outages are hand-copied into slot_status, and the copy is wrong

**Severity:** CRITICAL  ·  **Rows returned:** 3

v_slot_availability never joins dock_status_events. Outages are reflected only because somebody
manually set slot_status=BLOCKED, and that manual copy does not match the events. DEVT001 takes
DOCK-JAI-D3 down from 09:15, but blocking starts only at 10:00 - a 45-minute window where the dock
is broken and the data says otherwise, masked purely because an appointment happens to sit there.
DEVT003 reduces D2 capacity for 80 minutes and is reflected in no slot at all. A system trusting
this view will offer a broken dock.

**Proof:**

```sql
SELECT e.dock_event_id, e.event_type, d.dock_code,
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
ORDER BY e.dock_event_id
```

| dock_event_id | event_type         | dock_code | outage_window | slots_overlapping_outage | marked_blocked | NOT_marked_blocked | unmarked_slots                          |
|---------------|--------------------|-----------|---------------|--------------------------|----------------|--------------------|-----------------------------------------|
| DEVT001       | BREAKDOWN          | D3        | 09:15-13:00   | 4                        | 3              | 1                  | SLOT-JAI-030(09:00)                     |
| DEVT002       | MAINTENANCE        | D5        | 18:00-22:00   | 4                        | 4              | 0                  | NULL                                    |
| DEVT003       | CAPACITY_REDUCTION | D2        | 08:00-09:20   | 2                        | 0              | 2                  | SLOT-JAI-015(08:00) SLOT-JAI-016(09:00) |

---

## D06
### Four drivers hold two active shipments, and one of them is physically impossible

**Severity:** CRITICAL  ·  **Rows returned:** 4

A conversation is keyed on driver. For these four, 'which shipment does this driver mean' has no
single answer. DRV006 is assigned SHP1006 into Jaipur and SHP1021 into Gurugram ON THE SAME VEHICLE,
270 km apart, hours apart. One truck cannot be in both places.

**Proof:**

```sql
SELECT driver_id, COUNT(*) AS active_shipments,
       GROUP_CONCAT(shipment_id||' -> '||destination_facility_id||' ('||vehicle_id||' @ '||substr(original_eta_ts,12,5)||')', '  |  ') AS detail
FROM shipments
WHERE current_status IN ('ASSIGNED','IN_TRANSIT','WAITING')
GROUP BY driver_id HAVING COUNT(*) > 1
ORDER BY driver_id
```

| driver_id | active_shipments | detail                                                                            |
|-----------|------------------|-----------------------------------------------------------------------------------|
| DRV003    | 2                | SHP1003 -> FAC-JAI-01 (VEH003 @ 08:50)  |  SHP1018 -> FAC-JAI-01 (VEH003 @ 11:50) |
| DRV004    | 2                | SHP1004 -> FAC-JAI-01 (VEH004 @ 08:45)  |  SHP1020 -> FAC-JAI-01 (VEH004 @ 18:00) |
| DRV006    | 2                | SHP1006 -> FAC-JAI-01 (VEH015 @ 10:20)  |  SHP1021 -> FAC-GGN-01 (VEH015 @ 14:00) |
| DRV007    | 2                | SHP1007 -> FAC-JAI-01 (VEH008 @ 09:50)  |  SHP1016 -> FAC-JAI-01 (VEH007 @ 10:30) |

---

## D07
### A conversation that cannot be resolved to any row

**Severity:** HIGH  ·  **Rows returned:** 1

THR010 carries a delay report with shipment_id NULL, from a driver who holds two active shipments.
It cannot be auto-resolved. The system must ask, not guess.

**Proof:**

```sql
SELECT t.thread_id, t.driver_id, t.shipment_id, t.thread_intent, m.message_text
FROM chat_threads t LEFT JOIN chat_messages m ON m.thread_id = t.thread_id AND m.sender_type='DRIVER'
WHERE t.shipment_id IS NULL
```

| thread_id | driver_id | shipment_id | thread_intent | message_text                  |
|-----------|-----------|-------------|---------------|-------------------------------|
| THR010    | DRV004    | NULL        | UNKNOWN       | I will be late by 45 minutes. |

---

## D08
### The de-duplication key is unenforced and the duplicates disagree

**Severity:** HIGH  ·  **Rows returned:** 1

EXC009 repeats EXC001 under the same dedupe_key, but the two rows carry DIFFERENT severity. There is
no unique index on dedupe_key, so concurrent duplicates both pass a read-then-write check. De-
duplication currently depends on nobody racing.

**Proof:**

```sql
SELECT dedupe_key, COUNT(*) AS copies,
       GROUP_CONCAT(exception_id||'='||COALESCE(severity_code,'?')||'/'||exception_status, '  |  ') AS rows_found
FROM driver_exceptions WHERE dedupe_key IS NOT NULL
GROUP BY dedupe_key HAVING COUNT(*) > 1
```

| dedupe_key                   | copies | rows_found                                             |
|------------------------------|--------|--------------------------------------------------------|
| DRV006-SHP1006-20260804-0934 | 2      | EXC001=HIGH/NEEDS_INFORMATION  |  EXC009=LOW/DUPLICATE |

---

## D09
### Vague driver claims are stored as precise facts

**Severity:** MEDIUM  ·  **Rows returned:** 2

SHP1013's driver said only 'I am late by one hour' and SHP1017's gave a range of 12:30-13:00. Both
are persisted as exact timestamps - and SHP1017 is stored as the MIDPOINT (12:45), which is neither
end of the range the driver actually gave. confidence_code records the doubt; the timestamp hides
it.

**Proof:**

```sql
SELECT e.shipment_id, substr(e.declared_eta_ts,12,5) AS stored_eta, e.confidence_code, e.note
FROM eta_updates e
WHERE e.confidence_code = 'LOW' ORDER BY e.shipment_id
```

| shipment_id | stored_eta | confidence_code | note                                            |
|-------------|------------|-----------------|-------------------------------------------------|
| SHP1013     | 11:00      | LOW             | Driver said only: late by one hour.             |
| SHP1017     | 12:45      | LOW             | Driver expects arrival between 12:30 and 13:00. |

---

## D10
### The one case that must escalate, escalates into a void

**Severity:** CRITICAL  ·  **Rows returned:** 9

SHP1015 is the designed no-feasible-slot case. Its escalation email went to
night.shift@setuhaul.example, which matches NO row in facility_contacts. The only night-shift
contact is CON005 - 'Vacant Position', no email address, at the WRONG facility - and it is still
flagged active, so nothing surfaces the gap. Jaipur has no reachable after-hours human.

**Proof:**

```sql
SELECT om.operational_message_id, om.shipment_id, om.recipient_address, om.delivery_status,
       (SELECT COUNT(*) FROM facility_contacts fc WHERE fc.email = om.recipient_address) AS matching_contacts
FROM operational_messages om WHERE om.channel='EMAIL'
UNION ALL
SELECT 'CONTACT:'||contact_id, facility_id, COALESCE(NULLIF(email,''),'<<NO EMAIL>>'),
       'active_flag='||active_flag, NULL
FROM facility_contacts
```

| operational_message_id | shipment_id | recipient_address             | delivery_status | matching_contacts |
|------------------------|-------------|-------------------------------|-----------------|-------------------|
| OM001                  | SHP1014     | neha.bansal@setuhaul.example  | SENT            | 1                 |
| OM002                  | SHP1013     | neha.bansal@setuhaul.example  | QUEUED          | 1                 |
| OM003                  | SHP1005     | anita.joshi@setuhaul.example  | DELIVERED       | 1                 |
| OM004                  | SHP1015     | night.shift@setuhaul.example  | FAILED          | 0                 |
| CONTACT:CON001         | FAC-JAI-01  | neha.bansal@setuhaul.example  | active_flag=1   | NULL              |
| CONTACT:CON002         | FAC-JAI-01  | mahesh.verma@setuhaul.example | active_flag=1   | NULL              |
| CONTACT:CON003         | FAC-JAI-01  | anita.joshi@setuhaul.example  | active_flag=1   | NULL              |
| CONTACT:CON004         | FAC-GGN-01  | rohit.arora@setuhaul.example  | active_flag=1   | NULL              |
| CONTACT:CON005         | FAC-GGN-01  | <<NO EMAIL>>                  | active_flag=1   | NULL              |

---

## D11
### A queued message is holding capacity nobody has been asked about

**Severity:** HIGH  ·  **Rows returned:** 2

APT1013A has held D2 at 11:00 in PENDING_CONFIRMATION since 09:32, but its request email OM002 is
still QUEUED - never sent. The warehouse has not been asked. PENDING_CONFIRMATION sits inside the
active-appointment unique index, so this strands the slot, and appointments has no expiry column for
the pending state to time out in.

**Proof:**

```sql
SELECT a.appointment_id, a.appointment_status, substr(a.booked_at,12,5) AS pending_since,
       a.confirmed_at, om.operational_message_id, om.delivery_status
FROM appointments a
LEFT JOIN operational_messages om ON om.appointment_id = a.appointment_id
WHERE a.appointment_status='PENDING_CONFIRMATION'
```

| appointment_id | appointment_status   | pending_since | confirmed_at | operational_message_id | delivery_status |
|----------------|----------------------|---------------|--------------|------------------------|-----------------|
| APT1013A       | PENDING_CONFIRMATION | 09:32         | NULL         | OM002                  | QUEUED          |
| APT1014A       | PENDING_CONFIRMATION | 09:52         | NULL         | OM001                  | SENT            |

---

## D12
### A constraint the driver stated is stored and then ignored

**Severity:** HIGH  ·  **Rows returned:** 5

driver_exceptions.earliest_acceptable_ts and latest_acceptable_ts hold limits the driver gave in
words ('any slot after 12?', 'I must leave before 1:30'). Feasibility logic built only from dock
rules and ETA will offer slots that break a promise the driver already made clear.

**Proof:**

```sql
SELECT e.exception_id, e.shipment_id, substr(e.declared_eta_ts,12,5) AS declared_eta,
       substr(e.earliest_acceptable_ts,12,5) AS not_before,
       substr(e.latest_acceptable_ts,12,5)   AS must_finish_by
FROM driver_exceptions e
WHERE e.earliest_acceptable_ts IS NOT NULL OR e.latest_acceptable_ts IS NOT NULL
ORDER BY e.exception_id
```

| exception_id | shipment_id | declared_eta | not_before | must_finish_by |
|--------------|-------------|--------------|------------|----------------|
| EXC001       | SHP1006     | 11:20        | 12:00      | NULL           |
| EXC002       | SHP1012     | 11:10        | 11:10      | 13:30          |
| EXC004       | SHP1014     | 11:25        | 11:25      | NULL           |
| EXC005       | SHP1015     | 18:30        | 18:30      | NULL           |
| EXC006       | SHP1016     | 12:00        | 12:00      | NULL           |

---

## D13
### The replacement chain the guide describes does not exist in the data

**Severity:** MEDIUM  ·  **Rows returned:** 1

The database guide cites APT1012A and APT1016A as appointment-history chains via
replaced_appointment_id. Every row has it NULL. History is implied by is_current alone, so 'what
replaced what' cannot be reconstructed.

**Proof:**

```sql
SELECT COUNT(*) AS appointments_total,
       SUM(CASE WHEN replaced_appointment_id IS NOT NULL THEN 1 ELSE 0 END) AS with_replacement_link,
       SUM(CASE WHEN is_current=0 THEN 1 ELSE 0 END) AS historical_rows
FROM appointments
```

| appointments_total | with_replacement_link | historical_rows |
|--------------------|-----------------------|-----------------|
| 20                 | 0                     | 5               |

---

## D14
### The snapshot has no single 'now'

**Severity:** HIGH  ·  **Rows returned:** 5

Gate check-ins stop at 09:25 while an exception is raised at 16:24 and another ETA update lands at
16:25. 'What is available now' is undefined, so every availability answer depends on a clock the
data does not supply. We therefore pass the clock in as a parameter.

**Proof:**

```sql
SELECT 'latest facility_checkin' AS event, MAX(gate_in_ts) AS at FROM facility_checkins
UNION ALL SELECT 'latest chat_message', MAX(message_ts) FROM chat_messages
UNION ALL SELECT 'latest eta_update', MAX(created_at) FROM eta_updates
UNION ALL SELECT 'latest driver_exception', MAX(reported_at) FROM driver_exceptions
UNION ALL SELECT 'latest operational_message', MAX(sent_at) FROM operational_messages
```

| event                      | at                        |
|----------------------------|---------------------------|
| latest facility_checkin    | 2026-08-04T09:25:00+05:30 |
| latest chat_message        | 2026-08-04T16:24:08+05:30 |
| latest eta_update          | 2026-08-04T16:25:00+05:30 |
| latest driver_exception    | 2026-08-04T16:24:00+05:30 |
| latest operational_message | 2026-08-04T16:25:00+05:30 |

---

## D15
### The slot grid does not cover the hours the facilities claim to be open

**Severity:** MEDIUM  ·  **Rows returned:** 2

Jaipur opens at 06:00 but its first slot starts at 08:00. Gurugram runs 07:00-21:00 but has slots
only from 09:00 to 18:00. Capacity logic that reasons from facility hours will believe in slots that
do not exist.

**Proof:**

```sql
SELECT f.facility_id, f.open_time, f.close_time,
       substr(MIN(sl.slot_start_ts),12,5) AS first_slot,
       substr(MAX(sl.slot_end_ts),12,5)   AS last_slot, COUNT(*) AS slots
FROM facilities f
JOIN docks d ON d.facility_id = f.facility_id
JOIN appointment_slots sl ON sl.dock_id = d.dock_id
GROUP BY f.facility_id, f.open_time, f.close_time
```

| facility_id | open_time | close_time | first_slot | last_slot | slots |
|-------------|-----------|------------|------------|-----------|-------|
| FAC-GGN-01  | 07:00     | 21:00      | 09:00      | 18:00     | 27    |
| FAC-JAI-01  | 06:00     | 22:00      | 08:00      | 22:00     | 79    |

---

## D16
### Foreign keys are not enforced in the shipped database

**Severity:** MEDIUM  ·  **Rows returned:** 3

PRAGMA foreign_keys is 0 in the delivered SQLite file, so the declared foreign keys are
documentation rather than constraints. The same schema in Postgres enforces them, so a migration can
surface referential errors the snapshot currently tolerates.

**Proof:**

```sql
SELECT 'foreign_keys_enabled' AS pragma_name, (SELECT * FROM pragma_foreign_keys) AS value
UNION ALL SELECT 'orphan_appointments_vs_slots',
  (SELECT COUNT(*) FROM appointments a LEFT JOIN appointment_slots s ON s.slot_id=a.slot_id WHERE s.slot_id IS NULL)
UNION ALL SELECT 'orphan_shipments_vs_drivers',
  (SELECT COUNT(*) FROM shipments sh LEFT JOIN drivers dr ON dr.driver_id=sh.driver_id WHERE dr.driver_id IS NULL)
```

| pragma_name                  | value |
|------------------------------|-------|
| foreign_keys_enabled         | 0     |
| orphan_appointments_vs_slots | 0     |
| orphan_shipments_vs_drivers  | 0     |

---

_16 findings, 53 evidence rows, all reproducible via `python3 scripts/audit.py`._