ALTER TABLE usage_records
    ADD COLUMN input_tokens bigint NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    ADD COLUMN output_tokens bigint NOT NULL DEFAULT 0 CHECK (output_tokens >= 0);

CREATE INDEX usage_records_rollup_idx
    ON usage_records (organization_id, project_id, user_id, recorded_at);
