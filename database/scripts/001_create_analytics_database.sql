-- PREPARADO PARA REVISION. No ejecutado por el agente.
-- Ejecutar manualmente en el destino autorizado, fuera de una transaccion.
-- El nombre es deliberadamente fijo y distinto de WideWorldImporters.
USE [master];
GO
SET NOCOUNT ON;
SET IMPLICIT_TRANSACTIONS OFF;
IF @@TRANCOUNT <> 0
    THROW 51000, 'La creacion de base requiere una sesion sin transaccion abierta.', 1;

IF DB_ID(N'WideWorldImporters_Analytics') IS NULL
BEGIN
    CREATE DATABASE [WideWorldImporters_Analytics];
END;
GO
