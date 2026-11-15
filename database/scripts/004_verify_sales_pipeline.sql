-- Solo lectura. Ejecutar por el usuario despues de una publicacion exitosa.
-- Compara el corte persistido; no consulta un origen que pudo cambiar despues.
USE [WideWorldImporters_Analytics];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @lock int;
    EXEC @lock = sys.sp_getapplock @Resource=N'WWI:sales_to_warehouse',
        @LockMode='Shared', @LockOwner='Transaction', @LockTimeout=10000;
    IF @lock < 0 THROW 51020, 'No se obtuvo un corte estable para la verificacion.', 1;

    DECLARE @execution uniqueidentifier;
    SELECT @execution = execution_id FROM [audit].[etl_checkpoint]
        WHERE pipeline_name = 'sales_to_warehouse';
    IF @execution IS NULL THROW 51021, 'No existe una publicacion completa.', 1;

    SELECT * FROM [audit].[etl_checkpoint] WHERE pipeline_name = 'sales_to_warehouse';
    SELECT * FROM [audit].[etl_execution] WHERE execution_id = @execution;
    SELECT * FROM [audit].[etl_entity] WHERE execution_id = @execution;
    SELECT * FROM [audit].[etl_stage] WHERE execution_id = @execution ORDER BY started_at_utc;
    SELECT source_object, rule_id, severity, COUNT_BIG(*) AS Incidencias
        FROM [audit].[etl_quarantine] WHERE execution_id = @execution
        GROUP BY source_object, rule_id, severity;
    SELECT result_json AS EvidenciaReconciliacion FROM [audit].[etl_publication]
        WHERE execution_id = @execution;

    SELECT 'Customers' AS Entidad, 'Raw' AS Zona, COUNT_BIG(*) AS Filas
        FROM [raw].[Customers] WHERE execution_id = @execution
    UNION ALL SELECT 'Customers', 'Clean', COUNT_BIG(*) FROM [clean].[Customers] WHERE execution_id = @execution
    UNION ALL SELECT 'StockItems', 'Raw', COUNT_BIG(*) FROM [raw].[StockItems] WHERE execution_id = @execution
    UNION ALL SELECT 'StockItems', 'Clean', COUNT_BIG(*) FROM [clean].[StockItems] WHERE execution_id = @execution
    UNION ALL SELECT 'Invoices', 'Raw', COUNT_BIG(*) FROM [raw].[Invoices] WHERE execution_id = @execution
    UNION ALL SELECT 'Invoices', 'Clean', COUNT_BIG(*) FROM [clean].[Invoices] WHERE execution_id = @execution
    UNION ALL SELECT 'InvoiceLines', 'Raw', COUNT_BIG(*) FROM [raw].[InvoiceLines] WHERE execution_id = @execution
    UNION ALL SELECT 'InvoiceLines', 'Clean', COUNT_BIG(*) FROM [clean].[InvoiceLines] WHERE execution_id = @execution
    UNION ALL SELECT 'Ventas', 'Ready', COUNT_BIG(*) FROM [ready].[Ventas] WHERE execution_id = @execution
    UNION ALL SELECT 'Ventas', 'DW activas', COUNT_BIG(*) FROM [dw].[FactVentas] WHERE EsEliminadoFuente = 0;

    SELECT * FROM [dw].[vw_KPIVentas];
    SELECT MIN(InvoiceDate) AS PrimeraFecha, MAX(InvoiceDate) AS UltimaFecha,
           COUNT(DISTINCT YEAR(InvoiceDate)*100+MONTH(InvoiceDate)) AS MesesConObservaciones
        FROM [dw].[FactVentas] WHERE EsEliminadoFuente = 0;
    -- Mes sin observaciones no acredita cero ventas ni cobertura del origen.
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
