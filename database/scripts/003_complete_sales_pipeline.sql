-- Ejecutar manualmente despues de 001 y 002. No ejecutado por el agente.
-- Migracion aditiva: conserva todas las muestras Raw y sus identificadores.
USE [WideWorldImporters_Analytics];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'WideWorldImporters_Analytics'
    THROW 51010, 'Destino analitico incorrecto.', 1;
IF OBJECT_ID(N'audit.etl_execution', N'U') IS NULL
    THROW 51011, 'Ejecutar primero los scripts 001 y 002.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    IF SCHEMA_ID(N'clean') IS NULL EXEC(N'CREATE SCHEMA [clean] AUTHORIZATION [dbo];');
    IF SCHEMA_ID(N'ready') IS NULL EXEC(N'CREATE SCHEMA [ready] AUTHORIZATION [dbo];');
    IF SCHEMA_ID(N'dw') IS NULL EXEC(N'CREATE SCHEMA [dw] AUTHORIZATION [dbo];');
    IF COL_LENGTH(N'audit.etl_entity', N'source_rows_expected') IS NULL
        ALTER TABLE [audit].[etl_entity] ADD [source_rows_expected] bigint NULL;
    -- Orden tecnico de capturas; UUID y reloj del cliente no ordenan snapshots.
    IF COL_LENGTH(N'audit.etl_execution', N'capture_ordinal') IS NULL
        ALTER TABLE [audit].[etl_execution] ADD [capture_ordinal] bigint IDENTITY(1,1) NOT NULL;

    IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = N'CK_etl_entity_source'
               AND parent_object_id = OBJECT_ID(N'audit.etl_entity'))
        ALTER TABLE [audit].[etl_entity] DROP CONSTRAINT [CK_etl_entity_source];
    ALTER TABLE [audit].[etl_entity] WITH CHECK ADD CONSTRAINT [CK_etl_entity_source] CHECK
        (([source_schema] = N'Sales' AND [source_table] IN (N'Invoices', N'InvoiceLines', N'Customers'))
         OR ([source_schema] = N'Warehouse' AND [source_table] = N'StockItems'));

    IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = N'CK_etl_execution_status'
               AND parent_object_id = OBJECT_ID(N'audit.etl_execution'))
        ALTER TABLE [audit].[etl_execution] DROP CONSTRAINT [CK_etl_execution_status];
    ALTER TABLE [audit].[etl_execution] WITH CHECK ADD CONSTRAINT [CK_etl_execution_status] CHECK
        ([status] IN ('EXTRACTING', 'RAW_PARTIAL', 'RAW_COMPLETE', 'CLEANING',
                      'CLEAN_COMPLETE', 'READY_COMPLETE', 'PUBLISHING', 'PUBLISHED', 'BLOCKED', 'FAILED'));
    IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = N'CK_etl_execution_full'
               AND parent_object_id = OBJECT_ID(N'audit.etl_execution'))
        ALTER TABLE [audit].[etl_execution] DROP CONSTRAINT [CK_etl_execution_full];
    ALTER TABLE [audit].[etl_execution] WITH CHECK ADD CONSTRAINT [CK_etl_execution_full] CHECK
        ([status] NOT IN ('RAW_COMPLETE', 'CLEAN_COMPLETE', 'READY_COMPLETE', 'PUBLISHED')
         OR [max_rows_per_table] IS NULL);

    IF OBJECT_ID(N'raw.Customers', N'U') IS NULL
    BEGIN
        CREATE TABLE [raw].[Customers] (
            [execution_id] uniqueidentifier NOT NULL,
            [attempt_id] uniqueidentifier NOT NULL,
            [source_snapshot_id] uniqueidentifier NOT NULL,
            [extracted_at_utc] datetime2(7) NOT NULL,
            [schema_version] varchar(32) NOT NULL,
            [operation] varchar(8) NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            [CustomerID] int NOT NULL,
            [CustomerName] nvarchar(200) NOT NULL,
            [BillToCustomerID] int NOT NULL,
            [ValidFrom] datetime2(7) NOT NULL,
            [ValidTo] datetime2(7) NOT NULL,
            CONSTRAINT [PK_raw_Customers] PRIMARY KEY ([execution_id], [CustomerID]),
            CONSTRAINT [FK_raw_Customers_execution] FOREIGN KEY
                ([execution_id], [attempt_id], [source_snapshot_id]) REFERENCES
                [audit].[etl_execution] ([execution_id], [attempt_id], [source_snapshot_id]),
            CONSTRAINT [CK_raw_Customers_operation] CHECK ([operation] = 'SNAPSHOT')
        );
    END;

    IF OBJECT_ID(N'raw.StockItems', N'U') IS NULL
    BEGIN
        CREATE TABLE [raw].[StockItems] (
            [execution_id] uniqueidentifier NOT NULL,
            [attempt_id] uniqueidentifier NOT NULL,
            [source_snapshot_id] uniqueidentifier NOT NULL,
            [extracted_at_utc] datetime2(7) NOT NULL,
            [schema_version] varchar(32) NOT NULL,
            [operation] varchar(8) NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            [StockItemID] int NOT NULL,
            [StockItemName] nvarchar(200) NOT NULL,
            [Brand] nvarchar(100) NULL,
            [ValidFrom] datetime2(7) NOT NULL,
            [ValidTo] datetime2(7) NOT NULL,
            CONSTRAINT [PK_raw_StockItems] PRIMARY KEY ([execution_id], [StockItemID]),
            CONSTRAINT [FK_raw_StockItems_execution] FOREIGN KEY
                ([execution_id], [attempt_id], [source_snapshot_id]) REFERENCES
                [audit].[etl_execution] ([execution_id], [attempt_id], [source_snapshot_id]),
            CONSTRAINT [CK_raw_StockItems_operation] CHECK ([operation] = 'SNAPSHOT')
        );
    END;

    IF OBJECT_ID(N'clean.Customers', N'U') IS NULL
    BEGIN
        CREATE TABLE [clean].[Customers] (
            [execution_id] uniqueidentifier NOT NULL,
            [rules_version] varchar(32) NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            [CustomerID] int NOT NULL,
            [CustomerName] nvarchar(200) NOT NULL,
            [BillToCustomerID] int NOT NULL,
            [ValidFrom] datetime2(7) NOT NULL,
            [ValidTo] datetime2(7) NOT NULL,
            CONSTRAINT [PK_clean_Customers] PRIMARY KEY ([execution_id], [CustomerID]),
            CONSTRAINT [FK_clean_Customers_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id])
        );
    END;

    IF OBJECT_ID(N'clean.StockItems', N'U') IS NULL
    BEGIN
        CREATE TABLE [clean].[StockItems] (
            [execution_id] uniqueidentifier NOT NULL,
            [rules_version] varchar(32) NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            [StockItemID] int NOT NULL,
            [StockItemName] nvarchar(200) NOT NULL,
            [Brand] nvarchar(100) NULL,
            [ValidFrom] datetime2(7) NOT NULL,
            [ValidTo] datetime2(7) NOT NULL,
            CONSTRAINT [PK_clean_StockItems] PRIMARY KEY ([execution_id], [StockItemID]),
            CONSTRAINT [FK_clean_StockItems_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id])
        );
    END;

    IF OBJECT_ID(N'clean.Invoices', N'U') IS NULL
    BEGIN
        CREATE TABLE [clean].[Invoices] (
            [execution_id] uniqueidentifier NOT NULL,
            [rules_version] varchar(32) NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            [InvoiceID] int NOT NULL,
            [CustomerID] int NOT NULL,
            [BillToCustomerID] int NOT NULL,
            [InvoiceDate] date NOT NULL,
            [IsCreditNote] bit NOT NULL,
            [LastEditedWhen] datetime2(7) NOT NULL,
            CONSTRAINT [PK_clean_Invoices] PRIMARY KEY ([execution_id], [InvoiceID]),
            CONSTRAINT [FK_clean_Invoices_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id])
        );
    END;

    IF OBJECT_ID(N'clean.InvoiceLines', N'U') IS NULL
    BEGIN
        CREATE TABLE [clean].[InvoiceLines] (
            [execution_id] uniqueidentifier NOT NULL,
            [rules_version] varchar(32) NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            [InvoiceLineID] int NOT NULL,
            [InvoiceID] int NOT NULL,
            [StockItemID] int NOT NULL,
            [Quantity] int NOT NULL,
            [UnitPrice] decimal(18,2) NULL,
            [TaxAmount] decimal(18,2) NOT NULL,
            [ExtendedPrice] decimal(18,2) NOT NULL,
            [LineProfit] decimal(18,2) NOT NULL,
            [LastEditedWhen] datetime2(7) NOT NULL,
            CONSTRAINT [PK_clean_InvoiceLines] PRIMARY KEY ([execution_id], [InvoiceLineID]),
            CONSTRAINT [FK_clean_InvoiceLines_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id])
        );
    END;

    IF OBJECT_ID(N'ready.Ventas', N'U') IS NULL
    BEGIN
        CREATE TABLE [ready].[Ventas] (
            [execution_id] uniqueidentifier NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            [InvoiceLineID] int NOT NULL,
            [InvoiceID] int NOT NULL,
            [StockItemID] int NOT NULL,
            [CustomerID] int NOT NULL,
            [BillToCustomerID] int NOT NULL,
            [InvoiceDate] date NOT NULL,
            [IsCreditNote] bit NOT NULL,
            [Quantity] int NOT NULL,
            [UnitPrice] decimal(18,2) NULL,
            [TaxAmount] decimal(18,2) NOT NULL,
            [ExtendedPrice] decimal(18,2) NOT NULL,
            [LineProfit] decimal(18,2) NOT NULL,
            [ImporteBaseLinea] decimal(29,2) NULL,
            CONSTRAINT [PK_ready_Ventas] PRIMARY KEY ([execution_id], [InvoiceLineID]),
            CONSTRAINT [FK_ready_Ventas_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id])
        );
    END;

    IF OBJECT_ID(N'audit.etl_stage', N'U') IS NULL
    BEGIN
        CREATE TABLE [audit].[etl_stage] (
            [execution_id] uniqueidentifier NOT NULL,
            [attempt_id] uniqueidentifier NOT NULL,
            [stage_name] varchar(32) NOT NULL,
            [started_at_utc] datetime2(7) NOT NULL,
            [ended_at_utc] datetime2(7) NULL,
            [status] varchar(20) NOT NULL,
            [counts_json] nvarchar(max) NULL,
            [error_detail] nvarchar(1000) NULL,
            CONSTRAINT [PK_etl_stage] PRIMARY KEY ([execution_id], [attempt_id], [stage_name]),
            CONSTRAINT [FK_etl_stage_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id]),
            CONSTRAINT [CK_etl_stage_json] CHECK ([counts_json] IS NULL OR ISJSON([counts_json]) = 1)
        );
    END;
    IF OBJECT_ID(N'audit.etl_quarantine', N'U') IS NULL
    BEGIN
        CREATE TABLE [audit].[etl_quarantine] (
            [issue_id] bigint IDENTITY(1,1) NOT NULL CONSTRAINT [PK_etl_quarantine] PRIMARY KEY,
            [execution_id] uniqueidentifier NOT NULL,
            [attempt_id] uniqueidentifier NOT NULL,
            [source_object] nvarchar(256) NOT NULL,
            [source_key] nvarchar(128) NULL,
            [rule_id] varchar(32) NOT NULL,
            [severity] varchar(10) NOT NULL,
            [created_at_utc] datetime2(7) NOT NULL,
            [payload_json] nvarchar(max) NOT NULL,
            [detail] nvarchar(1000) NOT NULL,
            CONSTRAINT [FK_etl_quarantine_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id]),
            CONSTRAINT [CK_etl_quarantine_severity] CHECK ([severity] IN ('ERROR','WARNING')),
            CONSTRAINT [CK_etl_quarantine_json] CHECK (ISJSON([payload_json]) = 1)
        );
        CREATE INDEX [IX_etl_quarantine_run] ON [audit].[etl_quarantine]
            ([execution_id], [severity], [source_object]);
    END;
    IF OBJECT_ID(N'audit.etl_publication', N'U') IS NULL
    BEGIN
        CREATE TABLE [audit].[etl_publication] (
            [execution_id] uniqueidentifier NOT NULL CONSTRAINT [PK_etl_publication] PRIMARY KEY,
            [previous_execution_id] uniqueidentifier NULL,
            [published_at_utc] datetime2(7) NOT NULL,
            [result_json] nvarchar(max) NOT NULL,
            CONSTRAINT [FK_etl_publication_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id]),
            CONSTRAINT [CK_etl_publication_json] CHECK (ISJSON([result_json]) = 1)
        );
    END;
    IF OBJECT_ID(N'audit.etl_checkpoint', N'U') IS NULL
    BEGIN
        CREATE TABLE [audit].[etl_checkpoint] (
            [pipeline_name] varchar(64) NOT NULL CONSTRAINT [PK_etl_checkpoint] PRIMARY KEY,
            [execution_id] uniqueidentifier NOT NULL,
            [source_snapshot_id] uniqueidentifier NOT NULL,
            [source_instance] nvarchar(128) NOT NULL,
            [source_database] sysname NOT NULL,
            [schema_version] varchar(32) NOT NULL,
            [rules_version] varchar(32) NOT NULL,
            [manifest_vector] nvarchar(max) NOT NULL,
            [published_at_utc] datetime2(7) NOT NULL,
            CONSTRAINT [FK_etl_checkpoint_execution] FOREIGN KEY ([execution_id])
                REFERENCES [audit].[etl_execution] ([execution_id]),
            CONSTRAINT [CK_etl_checkpoint_json] CHECK (ISJSON([manifest_vector]) = 1)
        );
    END;
    IF OBJECT_ID(N'dw.DimFecha', N'U') IS NULL
    BEGIN
        CREATE TABLE [dw].[DimFecha] (
            [FechaKey] int NOT NULL CONSTRAINT [PK_DimFecha] PRIMARY KEY,
            [Fecha] date NULL,
            [Anio] int NULL, [Mes] int NULL, [Dia] int NULL, [Trimestre] int NULL,
            [DiaSemanaISO] int NULL, [EsFinDeSemana] bit NULL
        );
        CREATE UNIQUE INDEX [UX_DimFecha_Fecha] ON [dw].[DimFecha] ([Fecha]) WHERE [Fecha] IS NOT NULL;
    END;
    IF OBJECT_ID(N'dw.DimCliente', N'U') IS NULL
    BEGIN
        CREATE TABLE [dw].[DimCliente] (
            [ClienteKey] int IDENTITY(1,1) NOT NULL CONSTRAINT [PK_DimCliente] PRIMARY KEY,
            [CustomerID] int NULL, [CustomerName] nvarchar(200) NOT NULL,
            [BillToCustomerIDActual] int NULL,
            [EsEliminadoFuente] bit NOT NULL,
            [payload_hash] binary(32) NULL,
            [execution_id] uniqueidentifier NULL, [publication_id] uniqueidentifier NULL
        );
        CREATE UNIQUE INDEX [UX_DimCliente_CustomerID] ON [dw].[DimCliente] ([CustomerID])
            WHERE [CustomerID] IS NOT NULL;
    END;
    IF OBJECT_ID(N'dw.DimProducto', N'U') IS NULL
    BEGIN
        CREATE TABLE [dw].[DimProducto] (
            [ProductoKey] int IDENTITY(1,1) NOT NULL CONSTRAINT [PK_DimProducto] PRIMARY KEY,
            [StockItemID] int NULL, [StockItemName] nvarchar(200) NOT NULL,
            [Brand] nvarchar(100) NULL,
            [EsEliminadoFuente] bit NOT NULL,
            [payload_hash] binary(32) NULL,
            [execution_id] uniqueidentifier NULL, [publication_id] uniqueidentifier NULL
        );
        CREATE UNIQUE INDEX [UX_DimProducto_StockItemID] ON [dw].[DimProducto] ([StockItemID])
            WHERE [StockItemID] IS NOT NULL;
    END;
    IF OBJECT_ID(N'dw.FactVentas', N'U') IS NULL
    BEGIN
        CREATE TABLE [dw].[FactVentas] (
            [InvoiceLineID] int NOT NULL,
            [InvoiceID] int NOT NULL,
            [StockItemID] int NOT NULL,
            [CustomerID] int NOT NULL,
            [BillToCustomerID] int NOT NULL,
            [InvoiceDate] date NOT NULL,
            [IsCreditNote] bit NOT NULL,
            [Quantity] int NOT NULL,
            [UnitPrice] decimal(18,2) NULL,
            [TaxAmount] decimal(18,2) NOT NULL,
            [ExtendedPrice] decimal(18,2) NOT NULL,
            [LineProfit] decimal(18,2) NOT NULL,
            [ImporteBaseLinea] decimal(29,2) NULL,
            [FechaKey] int NOT NULL, [ClienteVentaKey] int NOT NULL,
            [ClienteFacturacionKey] int NOT NULL, [ProductoKey] int NOT NULL,
            [EsEliminadoFuente] bit NOT NULL,
            [payload_hash] binary(32) NOT NULL,
            [execution_id] uniqueidentifier NOT NULL, [publication_id] uniqueidentifier NOT NULL,
            CONSTRAINT [PK_FactVentas] PRIMARY KEY ([InvoiceLineID]),
            CONSTRAINT [FK_FactVentas_Fecha] FOREIGN KEY ([FechaKey]) REFERENCES [dw].[DimFecha] ([FechaKey]),
            CONSTRAINT [FK_FactVentas_ClienteVenta] FOREIGN KEY ([ClienteVentaKey]) REFERENCES [dw].[DimCliente] ([ClienteKey]),
            CONSTRAINT [FK_FactVentas_ClienteFacturacion] FOREIGN KEY ([ClienteFacturacionKey]) REFERENCES [dw].[DimCliente] ([ClienteKey]),
            CONSTRAINT [FK_FactVentas_Producto] FOREIGN KEY ([ProductoKey]) REFERENCES [dw].[DimProducto] ([ProductoKey])
        );
        CREATE INDEX [IX_FactVentas_Fecha] ON [dw].[FactVentas] ([FechaKey], [EsEliminadoFuente]);
    END;
    IF NOT EXISTS (SELECT 1 FROM [dw].[DimFecha] WHERE [FechaKey] = 0)
        INSERT [dw].[DimFecha] ([FechaKey]) VALUES (0);
    IF NOT EXISTS (SELECT 1 FROM [dw].[DimCliente] WHERE [ClienteKey] = 0)
    BEGIN
        SET IDENTITY_INSERT [dw].[DimCliente] ON;
        INSERT [dw].[DimCliente] ([ClienteKey], [CustomerID], [CustomerName], [BillToCustomerIDActual], [EsEliminadoFuente])
            VALUES (0, NULL, N'Desconocido', NULL, 0);
        SET IDENTITY_INSERT [dw].[DimCliente] OFF;
    END;
    IF NOT EXISTS (SELECT 1 FROM [dw].[DimProducto] WHERE [ProductoKey] = 0)
    BEGIN
        SET IDENTITY_INSERT [dw].[DimProducto] ON;
        INSERT [dw].[DimProducto] ([ProductoKey], [StockItemID], [StockItemName], [Brand], [EsEliminadoFuente])
            VALUES (0, NULL, N'Desconocido', NULL, 0);
        SET IDENTITY_INSERT [dw].[DimProducto] OFF;
    END;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

CREATE OR ALTER VIEW [dw].[vw_VentasActivas]
AS
    SELECT * FROM [dw].[FactVentas] WHERE [EsEliminadoFuente] = 0;
GO
CREATE OR ALTER VIEW [dw].[vw_KPIVentas]
AS
    SELECT COUNT_BIG(*) AS Lineas,
           CASE WHEN COUNT_BIG(*) <> COUNT_BIG(ImporteBaseLinea) THEN NULL
                ELSE COALESCE(SUM(CAST(ImporteBaseLinea AS decimal(38,2))), 0) END AS ImporteBaseFacturado,
           COALESCE(SUM(CAST(ExtendedPrice AS decimal(38,2))), 0) AS ImporteExtendido,
           COALESCE(SUM(CAST(Quantity AS bigint)), 0) AS UnidadesVendidas,
           COUNT_BIG(DISTINCT InvoiceID) AS FacturasDistintas,
           COALESCE(SUM(CAST(LineProfit AS decimal(38,2))), 0) AS UtilidadRegistrada,
           CASE WHEN COUNT_BIG(*) = 0 OR COUNT_BIG(*) <> COUNT_BIG(ImporteBaseLinea) THEN NULL
                ELSE CAST(CAST(SUM(LineProfit) AS decimal(28,2)) * CAST(100 AS decimal(5,2))
                     / NULLIF(CAST(SUM(ImporteBaseLinea) AS decimal(28,2)), 0) AS decimal(38,6)) END AS MargenUtilidad,
           CASE WHEN COUNT_BIG(*) = 0 THEN 'SIN_DATOS'
                WHEN COUNT_BIG(*) <> COUNT_BIG(ImporteBaseLinea) THEN 'INCOMPLETO'
                WHEN SUM(ImporteBaseLinea) = 0 THEN 'DENOMINADOR_CERO'
                ELSE 'CALCULABLE' END AS EstadoBaseMargen
    FROM [dw].[FactVentas] WHERE [EsEliminadoFuente] = 0;
GO
