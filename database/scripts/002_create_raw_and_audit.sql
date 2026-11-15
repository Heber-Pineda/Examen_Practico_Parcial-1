-- PREPARADO PARA REVISION. Ejecutar despues de 001, nunca en la base fuente.
-- Bootstrap repetible: crea objetos ausentes; no migra estructuras incompatibles.
USE [WideWorldImporters_Analytics];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'WideWorldImporters_Analytics'
    THROW 51001, 'Destino analitico incorrecto.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    IF SCHEMA_ID(N'audit') IS NULL
        EXEC(N'CREATE SCHEMA [audit] AUTHORIZATION [dbo];');
    IF SCHEMA_ID(N'raw') IS NULL
        EXEC(N'CREATE SCHEMA [raw] AUTHORIZATION [dbo];');

    IF OBJECT_ID(N'audit.etl_execution', N'U') IS NULL
    BEGIN
        CREATE TABLE [audit].[etl_execution] (
            [execution_id] uniqueidentifier NOT NULL,
            [attempt_id] uniqueidentifier NOT NULL,
            [source_snapshot_id] uniqueidentifier NOT NULL,
            [pipeline_name] varchar(64) NOT NULL,
            [schema_version] varchar(32) NOT NULL,
            [rules_version] varchar(32) NOT NULL,
            [source_system] nvarchar(64) NOT NULL,
            [source_instance] nvarchar(128) NOT NULL,
            [source_database] sysname NOT NULL,
            [target_instance] nvarchar(128) NOT NULL,
            [target_database] sysname NOT NULL,
            [consistency_mode] varchar(20) NOT NULL,
            [max_rows_per_table] int NULL,
            [started_at_utc] datetime2(7) NOT NULL,
            [ended_at_utc] datetime2(7) NULL,
            [status] varchar(20) NOT NULL,
            [error_type] varchar(128) NULL,
            [error_code] varchar(32) NULL,
            [error_detail] nvarchar(1000) NULL,
            CONSTRAINT [PK_etl_execution] PRIMARY KEY ([execution_id]),
            CONSTRAINT [UQ_etl_execution_identity] UNIQUE
                ([execution_id], [attempt_id], [source_snapshot_id]),
            CONSTRAINT [CK_etl_execution_status] CHECK
                ([status] IN ('EXTRACTING', 'RAW_PARTIAL', 'RAW_COMPLETE', 'FAILED')),
            CONSTRAINT [CK_etl_execution_consistency] CHECK
                ([consistency_mode] IN ('snapshot', 'stable-window')),
            CONSTRAINT [CK_etl_execution_limit] CHECK
                ([max_rows_per_table] IS NULL OR [max_rows_per_table] > 0),
            CONSTRAINT [CK_etl_execution_full] CHECK
                ([status] <> 'RAW_COMPLETE' OR [max_rows_per_table] IS NULL)
        );
    END;

    IF OBJECT_ID(N'audit.etl_entity', N'U') IS NULL
    BEGIN
        CREATE TABLE [audit].[etl_entity] (
            [execution_id] uniqueidentifier NOT NULL,
            [source_schema] sysname NOT NULL,
            [source_table] sysname NOT NULL,
            [target_schema] sysname NOT NULL,
            [target_table] sysname NOT NULL,
            [started_at_utc] datetime2(7) NOT NULL,
            [ended_at_utc] datetime2(7) NULL,
            [status] varchar(20) NOT NULL,
            [rows_read] bigint NOT NULL CONSTRAINT [DF_etl_entity_read] DEFAULT 0,
            [rows_inserted] bigint NOT NULL CONSTRAINT [DF_etl_entity_inserted] DEFAULT 0,
            -- NULL significa fase no aplicada, no cero rechazos ni cero cambios.
            [rows_updated] bigint NULL,
            [rows_rejected] bigint NULL,
            [batches_committed] int NOT NULL CONSTRAINT [DF_etl_entity_batches] DEFAULT 0,
            [observed_min_LastEditedWhen] datetime2(7) NULL,
            [observed_max_LastEditedWhen] datetime2(7) NULL,
            [manifest_hash] binary(32) NULL,
            [is_complete] bit NOT NULL CONSTRAINT [DF_etl_entity_complete] DEFAULT 0,
            CONSTRAINT [PK_etl_entity] PRIMARY KEY ([execution_id], [source_table]),
            CONSTRAINT [FK_etl_entity_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id]),
            CONSTRAINT [CK_etl_entity_source] CHECK
                ([source_schema] = N'Sales' AND [source_table] IN (N'Invoices', N'InvoiceLines')),
            CONSTRAINT [CK_etl_entity_status] CHECK
                ([status] IN ('EXTRACTING', 'RAW_PARTIAL', 'RAW_COMPLETE', 'FAILED')),
            CONSTRAINT [CK_etl_entity_counts] CHECK
                ([rows_read] >= 0 AND [rows_inserted] >= 0 AND [batches_committed] >= 0)
        );
    END;

    IF OBJECT_ID(N'raw.Invoices', N'U') IS NULL
    BEGIN
        CREATE TABLE [raw].[Invoices] (
            -- Metadatos tecnicos ETL; no son campos de WideWorldImporters.
            [execution_id] uniqueidentifier NOT NULL,
            [attempt_id] uniqueidentifier NOT NULL,
            [source_snapshot_id] uniqueidentifier NOT NULL,
            [extracted_at_utc] datetime2(7) NOT NULL,
            [schema_version] varchar(32) NOT NULL,
            [operation] varchar(8) NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            -- Proyeccion contractual de Sales.Invoices.
            [InvoiceID] int NOT NULL,
            [CustomerID] int NOT NULL,
            [BillToCustomerID] int NOT NULL,
            [InvoiceDate] date NOT NULL,
            [IsCreditNote] bit NOT NULL,
            [LastEditedWhen] datetime2(7) NOT NULL,
            CONSTRAINT [PK_raw_Invoices] PRIMARY KEY ([execution_id], [InvoiceID]),
            CONSTRAINT [FK_raw_Invoices_execution] FOREIGN KEY
                ([execution_id], [attempt_id], [source_snapshot_id]) REFERENCES
                [audit].[etl_execution] ([execution_id], [attempt_id], [source_snapshot_id]),
            CONSTRAINT [CK_raw_Invoices_operation] CHECK ([operation] = 'SNAPSHOT')
        );
    END;

    IF OBJECT_ID(N'raw.InvoiceLines', N'U') IS NULL
    BEGIN
        CREATE TABLE [raw].[InvoiceLines] (
            [execution_id] uniqueidentifier NOT NULL,
            [attempt_id] uniqueidentifier NOT NULL,
            [source_snapshot_id] uniqueidentifier NOT NULL,
            [extracted_at_utc] datetime2(7) NOT NULL,
            [schema_version] varchar(32) NOT NULL,
            [operation] varchar(8) NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            -- Proyeccion contractual de Sales.InvoiceLines; UnitPrice admite NULL.
            [InvoiceLineID] int NOT NULL,
            [InvoiceID] int NOT NULL,
            [StockItemID] int NOT NULL,
            [Quantity] int NOT NULL,
            [UnitPrice] decimal(18,2) NULL,
            [TaxAmount] decimal(18,2) NOT NULL,
            [ExtendedPrice] decimal(18,2) NOT NULL,
            [LineProfit] decimal(18,2) NOT NULL,
            [LastEditedWhen] datetime2(7) NOT NULL,
            CONSTRAINT [PK_raw_InvoiceLines] PRIMARY KEY ([execution_id], [InvoiceLineID]),
            CONSTRAINT [FK_raw_InvoiceLines_execution] FOREIGN KEY
                ([execution_id], [attempt_id], [source_snapshot_id]) REFERENCES
                [audit].[etl_execution] ([execution_id], [attempt_id], [source_snapshot_id]),
            CONSTRAINT [CK_raw_InvoiceLines_operation] CHECK ([operation] = 'SNAPSHOT')
            -- Sin FK comercial a cabecera/producto: Raw conserva lo recibido;
            -- la integridad de negocio se comprobara posteriormente en Clean/Ready.
        );
    END;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
