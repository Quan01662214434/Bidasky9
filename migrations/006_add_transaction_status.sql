-- Add status column to transactions to support cancellation
ALTER TABLE transactions ADD COLUMN status TEXT DEFAULT 'completed';
