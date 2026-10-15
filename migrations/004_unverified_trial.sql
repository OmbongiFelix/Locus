-- migrations/004_unverified_trial.sql
-- Add unverified_request_count column to users table to support the 5-request trial period for unverified accounts.

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS unverified_request_count INT NOT NULL DEFAULT 0;
