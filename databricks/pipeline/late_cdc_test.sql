-- Late-data test for AUTO CDC, in its own schema (workspace.approval_copilot_cdc): it reads a small feed of
-- acquirer status events and never touches the copilot's tables. Run by the approval_copilot_late_cdc job, which
-- lands the feed in two batches, the second one late and out of order, and checks the result (notebook 13).

CREATE OR REFRESH STREAMING TABLE payment_status_events
COMMENT 'Acquirer status events for a few payments, in the order they arrived, late ones included'
AS SELECT * FROM STREAM(workspace.approval_copilot_cdc.payment_status_feed);

-- The latest status per payment by the acquirer's sequence number, not by arrival. A late event older than one
-- already applied changes nothing; a later one replaces it; a DELETE removes the payment.
CREATE OR REFRESH STREAMING TABLE payment_status_current
COMMENT 'Latest status of each payment by acquirer sequence (SCD type 1); a late, older event does not overwrite it';

CREATE FLOW payment_status_current_flow AS AUTO CDC INTO payment_status_current
FROM STREAM(payment_status_events)
KEYS (payment_id)
APPLY AS DELETE WHEN op = 'DELETE'
SEQUENCE BY sequence_num
COLUMNS * EXCEPT (op, batch, received_at_utc)
STORED AS SCD TYPE 1;

-- Every status each payment went through, in sequence order, with the span each one was current (__START_AT and
-- __END_AT hold sequence numbers). A late event is placed where its sequence number says.
CREATE OR REFRESH STREAMING TABLE payment_status_history
COMMENT 'Every status of each payment in acquirer sequence (SCD type 2), late events placed by sequence';

CREATE FLOW payment_status_history_flow AS AUTO CDC INTO payment_status_history
FROM STREAM(payment_status_events)
KEYS (payment_id)
APPLY AS DELETE WHEN op = 'DELETE'
SEQUENCE BY sequence_num
COLUMNS * EXCEPT (op, batch, received_at_utc)
STORED AS SCD TYPE 2;
