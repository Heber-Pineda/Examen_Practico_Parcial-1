-- Comprobación manual después de 005 y la demostración. Solo SELECT; no ejecutado por el agente.
USE [WideWorldImporters_Analytics];
GO
SET NOCOUNT ON;
SELECT TOP (20) evaluation_id,evaluated_at_utc,period,source_publication,mode,
    current_amount,previous_amount,change_pct,threshold_pct,status,event_id,condition_text
FROM ops.alert_evaluation ORDER BY evaluated_at_utc DESC;

SELECT TOP (20) e.event_id,e.entity_key,e.source_publication,e.delivery_status,e.attempts,
    e.last_error_code,e.next_attempt_at_utc,n.notification_id,n.delivered_at_utc,n.read_at_utc
FROM ops.alert_event e LEFT JOIN ops.notification n ON n.event_id=e.event_id
ORDER BY e.created_at_utc DESC;

SELECT TOP (30) event_id,attempt_number,started_at_utc,finished_at_utc,status,error_code
FROM ops.delivery_attempt ORDER BY started_at_utc DESC;

-- Debe devolver cero filas: la unicidad evita entregas duplicadas por evento.
SELECT event_id,COUNT_BIG(*) AS copies FROM ops.notification GROUP BY event_id HAVING COUNT_BIG(*)>1;
