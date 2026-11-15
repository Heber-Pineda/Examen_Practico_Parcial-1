-- Ejecutar manualmente en Analytics después de 003. Aditivo; no modifica DW/ETL.
USE [WideWorldImporters_Analytics];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'WideWorldImporters_Analytics' THROW 51100, 'Destino incorrecto.', 1;
IF OBJECT_ID(N'dw.FactVentas', N'U') IS NULL THROW 51101, 'Falta el DW.', 1;
IF SCHEMA_ID(N'ops') IS NULL EXEC(N'CREATE SCHEMA ops AUTHORIZATION dbo;');

IF OBJECT_ID(N'ops.alert_event', N'U') IS NULL
CREATE TABLE ops.alert_event (
    event_id uniqueidentifier NOT NULL CONSTRAINT PK_alert_event PRIMARY KEY,
    dedup_key char(64) NOT NULL CONSTRAINT UQ_alert_event_dedup UNIQUE,
    rule_id varchar(64) NOT NULL, rule_version varchar(32) NOT NULL,
    event_type varchar(40) NOT NULL, severity varchar(16) NOT NULL,
    entity_type varchar(32) NOT NULL, entity_key char(7) NOT NULL,
    source_publication uniqueidentifier NOT NULL,
    is_demo bit NOT NULL CONSTRAINT CK_alert_demo CHECK (is_demo = 1),
    payload_json nvarchar(max) NOT NULL CONSTRAINT CK_alert_json CHECK (ISJSON(payload_json) = 1),
    created_at_utc datetime2(7) NOT NULL CONSTRAINT DF_alert_created DEFAULT SYSUTCDATETIME(),
    delivery_status varchar(16) NOT NULL CONSTRAINT CK_alert_delivery CHECK (delivery_status IN ('PENDING','PROCESSING','RETRY','DELIVERED','FAILED')),
    attempts int NOT NULL CONSTRAINT DF_alert_attempts DEFAULT 0,
    next_attempt_at_utc datetime2(7) NULL,
    lease_token uniqueidentifier NULL, lease_until_utc datetime2(7) NULL,
    last_error_code varchar(64) NULL
);
IF OBJECT_ID(N'ops.alert_evaluation', N'U') IS NULL
CREATE TABLE ops.alert_evaluation (
    evaluation_id uniqueidentifier NOT NULL CONSTRAINT PK_alert_evaluation PRIMARY KEY,
    evaluated_at_utc datetime2(7) NOT NULL CONSTRAINT DF_evaluation_date DEFAULT SYSUTCDATETIME(),
    rule_id varchar(64) NOT NULL, rule_version varchar(32) NOT NULL,
    source_publication uniqueidentifier NOT NULL, period char(7) NOT NULL,
    condition_text nvarchar(500) NOT NULL, mode varchar(24) NOT NULL,
    current_amount decimal(38,2) NULL, previous_amount decimal(38,2) NULL,
    change_pct decimal(28,6) NULL, threshold_pct decimal(28,6) NOT NULL,
    status varchar(24) NOT NULL CONSTRAINT CK_evaluation_status CHECK (status IN ('TRIGGERED','NOT_TRIGGERED','INSUFFICIENT_DATA')),
    event_id uniqueidentifier NULL CONSTRAINT FK_evaluation_event REFERENCES ops.alert_event(event_id)
);
IF OBJECT_ID(N'ops.delivery_attempt', N'U') IS NULL
CREATE TABLE ops.delivery_attempt (
    event_id uniqueidentifier NOT NULL CONSTRAINT FK_attempt_event REFERENCES ops.alert_event(event_id),
    attempt_number int NOT NULL, started_at_utc datetime2(7) NOT NULL,
    finished_at_utc datetime2(7) NULL, status varchar(16) NOT NULL,
    error_code varchar(64) NULL,
    CONSTRAINT PK_delivery_attempt PRIMARY KEY(event_id, attempt_number),
    CONSTRAINT CK_attempt_status CHECK (status IN ('PROCESSING','RETRY','DELIVERED','FAILED'))
);
IF OBJECT_ID(N'ops.notification', N'U') IS NULL
CREATE TABLE ops.notification (
    notification_id uniqueidentifier NOT NULL CONSTRAINT PK_notification PRIMARY KEY,
    event_id uniqueidentifier NOT NULL CONSTRAINT UQ_notification_event UNIQUE
        CONSTRAINT FK_notification_event REFERENCES ops.alert_event(event_id),
    title nvarchar(200) NOT NULL, body nvarchar(1800) NOT NULL,
    delivered_at_utc datetime2(7) NOT NULL CONSTRAINT DF_notification_date DEFAULT SYSUTCDATETIME(),
    read_at_utc datetime2(7) NULL
);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'ops.alert_event') AND name=N'IX_alert_pending')
CREATE INDEX IX_alert_pending ON ops.alert_event(delivery_status, next_attempt_at_utc, created_at_utc);
GO

CREATE OR ALTER PROCEDURE ops.usp_alert_record
    @evaluation_id uniqueidentifier, @publication uniqueidentifier, @period char(7),
    @condition nvarchar(500), @mode varchar(24), @current decimal(38,2), @previous decimal(38,2),
    @change decimal(28,6), @threshold decimal(28,6), @status varchar(24), @dedup char(64), @payload nvarchar(max)
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    BEGIN TRY
        BEGIN TRANSACTION;
        IF NOT EXISTS (SELECT 1 FROM audit.etl_checkpoint WITH(HOLDLOCK)
                       WHERE pipeline_name='sales_to_warehouse' AND execution_id=@publication)
            THROW 51102, 'La publicacion cambio; evaluar nuevamente.', 1;
        IF @status NOT IN ('TRIGGERED','NOT_TRIGGERED','INSUFFICIENT_DATA') OR ISJSON(@payload)<>1
            THROW 51103, 'Evaluacion invalida.', 1;
        DECLARE @event uniqueidentifier = NULL;
        IF @status='TRIGGERED'
        BEGIN
            IF @dedup IS NULL OR LEN(@dedup)<>64 THROW 51104, 'Falta deduplicacion.', 1;
            SELECT @event=event_id FROM ops.alert_event WITH(UPDLOCK,HOLDLOCK) WHERE dedup_key=@dedup;
            IF @event IS NULL
            BEGIN
                SET @event=NEWID();
                INSERT ops.alert_event(event_id,dedup_key,rule_id,rule_version,event_type,severity,entity_type,
                    entity_key,source_publication,is_demo,payload_json,delivery_status,next_attempt_at_utc)
                VALUES(@event,@dedup,'DEMO_SALES_MOM','1','MONTHLY_SALES_CHANGE','WARNING','SALES_MONTH',
                    @period,@publication,1,@payload,'PENDING',SYSUTCDATETIME());
            END;
        END;
        IF NOT EXISTS (SELECT 1 FROM ops.alert_evaluation WHERE evaluation_id=@evaluation_id)
        INSERT ops.alert_evaluation(evaluation_id,rule_id,rule_version,source_publication,period,condition_text,mode,
            current_amount,previous_amount,change_pct,threshold_pct,status,event_id)
        VALUES(@evaluation_id,'DEMO_SALES_MOM','1',@publication,@period,@condition,@mode,
            @current,@previous,@change,@threshold,@status,@event);
        COMMIT;
        SELECT @evaluation_id AS evaluation_id, @event AS event_id, @status AS status;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_alert_claim
    @lease uniqueidentifier, @max_attempts int, @retry_now bit
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    IF @max_attempts NOT BETWEEN 1 AND 10 THROW 51105, 'Limite de intentos invalido.', 1;
    BEGIN TRY
        BEGIN TRANSACTION;
        DECLARE @now datetime2(7)=SYSUTCDATETIME(), @event uniqueidentifier;
        UPDATE a SET finished_at_utc=@now, error_code='LEASE_EXPIRED',
            status=CASE WHEN e.attempts>=@max_attempts THEN 'FAILED' ELSE 'RETRY' END
        FROM ops.delivery_attempt a JOIN ops.alert_event e ON a.event_id=e.event_id AND a.attempt_number=e.attempts
        WHERE e.delivery_status='PROCESSING' AND e.lease_until_utc<@now AND a.status='PROCESSING';
        UPDATE ops.alert_event SET delivery_status='FAILED', last_error_code='LEASE_EXPIRED',lease_token=NULL,lease_until_utc=NULL
        WHERE delivery_status='PROCESSING' AND lease_until_utc<@now AND attempts>=@max_attempts;
        UPDATE ops.alert_event SET delivery_status='FAILED',last_error_code=COALESCE(last_error_code,'MAX_ATTEMPTS_REACHED')
        WHERE delivery_status IN ('PENDING','RETRY') AND attempts>=@max_attempts;
        -- Fuerza lectura con locks también si el destino utiliza RCSI.
        SELECT TOP(1) @event=event_id FROM ops.alert_event WITH(UPDLOCK,READPAST,READCOMMITTEDLOCK)
        WHERE attempts<@max_attempts AND
            ((delivery_status IN ('PENDING','RETRY') AND (next_attempt_at_utc<=@now OR @retry_now=1))
             OR (delivery_status='PROCESSING' AND lease_until_utc<@now))
        ORDER BY created_at_utc,event_id;
        IF @event IS NOT NULL
        BEGIN
            UPDATE ops.alert_event SET delivery_status='PROCESSING',attempts=attempts+1,lease_token=@lease,
                lease_until_utc=DATEADD(minute,3,@now) WHERE event_id=@event;
            INSERT ops.delivery_attempt(event_id,attempt_number,started_at_utc,status)
                SELECT event_id,attempts,@now,'PROCESSING' FROM ops.alert_event WHERE event_id=@event;
        END;
        COMMIT;
        SELECT event_id,payload_json,attempts,lease_token FROM ops.alert_event WHERE event_id=@event AND lease_token=@lease;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_alert_deliver @event uniqueidentifier, @lease uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    BEGIN TRY
        BEGIN TRANSACTION;
        DECLARE @payload nvarchar(max), @state varchar(16);
        SELECT @payload=payload_json,@state=delivery_status FROM ops.alert_event WITH(UPDLOCK,HOLDLOCK) WHERE event_id=@event;
        IF @state IS NULL THROW 51106, 'Evento inexistente.', 1;
        IF @state<>'DELIVERED'
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM ops.alert_event WHERE event_id=@event AND delivery_status='PROCESSING'
                           AND lease_token=@lease AND lease_until_utc>=SYSUTCDATETIME())
                THROW 51107, 'Reserva no vigente.', 1;
            IF NOT EXISTS (SELECT 1 FROM ops.notification WHERE event_id=@event)
                INSERT ops.notification(notification_id,event_id,title,body)
                VALUES(@event,@event,JSON_VALUE(@payload,'$.title'),JSON_VALUE(@payload,'$.body'));
            UPDATE ops.delivery_attempt SET status='DELIVERED',finished_at_utc=SYSUTCDATETIME()
                WHERE event_id=@event AND status='PROCESSING';
            UPDATE ops.alert_event SET delivery_status='DELIVERED',lease_token=NULL,lease_until_utc=NULL,last_error_code=NULL
                WHERE event_id=@event;
        END;
        COMMIT;
        SELECT @event AS event_id,'DELIVERED' AS status;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_alert_retry
    @event uniqueidentifier, @lease uniqueidentifier, @code varchar(64), @max_attempts int, @delay_seconds int
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    IF @delay_seconds NOT BETWEEN 5 AND 3600 OR @max_attempts NOT BETWEEN 1 AND 10
        THROW 51108, 'Limites de reintento invalidos.', 1;
    BEGIN TRY
        BEGIN TRANSACTION;
        DECLARE @state varchar(16);
        SELECT @state=delivery_status FROM ops.alert_event WITH(UPDLOCK,HOLDLOCK) WHERE event_id=@event;
        IF @state='PROCESSING' AND EXISTS(SELECT 1 FROM ops.alert_event WHERE event_id=@event AND lease_token=@lease)
        BEGIN
            UPDATE ops.alert_event SET delivery_status=CASE WHEN attempts>=@max_attempts THEN 'FAILED' ELSE 'RETRY' END,
                next_attempt_at_utc=DATEADD(second,@delay_seconds,SYSUTCDATETIME()),last_error_code=@code,
                lease_token=NULL,lease_until_utc=NULL WHERE event_id=@event;
            UPDATE a SET status=e.delivery_status,finished_at_utc=SYSUTCDATETIME(),error_code=@code
                FROM ops.delivery_attempt a JOIN ops.alert_event e ON e.event_id=a.event_id AND e.attempts=a.attempt_number
                WHERE a.event_id=@event AND a.status='PROCESSING';
        END;
        COMMIT;
        SELECT event_id,delivery_status AS status FROM ops.alert_event WHERE event_id=@event;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_notification_read @notification uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    UPDATE ops.notification SET read_at_utc=COALESCE(read_at_utc,SYSUTCDATETIME()) WHERE notification_id=@notification;
    SELECT notification_id,read_at_utc FROM ops.notification WHERE notification_id=@notification;
END;
GO

-- Asignar estos roles a las identidades Windows elegidas, manualmente; sin contraseñas.
IF DATABASE_PRINCIPAL_ID(N'wwi_alert_worker') IS NULL CREATE ROLE wwi_alert_worker;
IF DATABASE_PRINCIPAL_ID(N'wwi_notification_api') IS NULL CREATE ROLE wwi_notification_api;
GRANT SELECT ON dw.FactVentas TO wwi_alert_worker;
GRANT SELECT ON audit.etl_checkpoint TO wwi_alert_worker;
GRANT SELECT ON audit.etl_publication TO wwi_alert_worker;
GRANT EXECUTE ON ops.usp_alert_record TO wwi_alert_worker;
GRANT EXECUTE ON ops.usp_alert_claim TO wwi_alert_worker;
GRANT EXECUTE ON ops.usp_alert_deliver TO wwi_alert_worker;
GRANT EXECUTE ON ops.usp_alert_retry TO wwi_alert_worker;
GRANT SELECT ON ops.notification TO wwi_notification_api;
GRANT SELECT ON ops.alert_event TO wwi_notification_api;
GRANT EXECUTE ON ops.usp_notification_read TO wwi_notification_api;
GO
