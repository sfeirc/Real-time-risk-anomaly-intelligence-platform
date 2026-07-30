//! Batched ClickHouse sink over the HTTP interface. Batches (not
//! one-row-per-insert) because ClickHouse's MergeTree write path is
//! optimized for bulk inserts — many small inserts create excessive parts
//! and force constant background merges.

use crate::model::FeatureEvent;

pub struct ClickHouseSink {
    client: reqwest::Client,
    base_url: String,
    database: String,
    user: String,
    password: String,
}

impl ClickHouseSink {
    pub fn new(base_url: String, database: String, user: String, password: String) -> Self {
        Self { client: reqwest::Client::new(), base_url, database, user, password }
    }

    pub async fn insert_features(&self, rows: &[FeatureEvent]) -> Result<(), String> {
        if rows.is_empty() {
            return Ok(());
        }
        let mut body = String::new();
        for row in rows {
            let line = serde_json::to_string(row).map_err(|e| e.to_string())?;
            body.push_str(&line);
            body.push('\n');
        }

        let mut url = reqwest::Url::parse(&format!("{}/", self.base_url)).map_err(|e| e.to_string())?;
        url.query_pairs_mut()
            .append_pair("database", &self.database)
            .append_pair("query", "INSERT INTO features FORMAT JSONEachRow")
            // FeatureEvent serializes timestamps as RFC3339 (`...T...+00:00`)
            // to match docs/data-contracts.md for the Kafka payload too;
            // ClickHouse's default DateTime64 parser only accepts
            // `YYYY-MM-DD HH:MM:SS.sss`. best_effort parses RFC3339 directly
            // instead of needing a ClickHouse-specific timestamp format.
            .append_pair("date_time_input_format", "best_effort");

        let resp = self
            .client
            .post(url)
            .basic_auth(&self.user, Some(&self.password))
            .body(body)
            .send()
            .await
            .map_err(|e| e.to_string())?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(format!("clickhouse insert failed: {status} {text}"));
        }
        Ok(())
    }
}
