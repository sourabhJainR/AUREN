use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Request {
    Sha256 { value: String },
    ResourceRoute { task_class: String, observations: Vec<ResourceObservation>, lanes: Vec<String> },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResourceObservation {
    pub lane: String,
    pub task_class: String,
    pub duration: f64,
    pub memory_mb: u64,
    pub success: bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Response {
    pub ok: bool,
    pub op: String,
    pub value: serde_json::Value,
}

pub fn sha256_hex(value: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(value);
    format!("{:x}", hasher.finalize())
}

pub fn route_resource(task_class: &str, observations: &[ResourceObservation], lanes: &[String]) -> String {
    let mut scores: BTreeMap<String, (u64, u64)> = BTreeMap::new();
    for lane in lanes {
        scores.insert(lane.clone(), (0, 0));
    }
    for row in observations.iter().filter(|r| r.task_class == task_class) {
        let entry = scores.entry(row.lane.clone()).or_insert((0, 0));
        entry.0 += row.success as u64;
        entry.1 += 1;
    }
    scores.into_iter()
        .max_by(|(lane_a, (ok_a, n_a)), (lane_b, (ok_b, n_b))| {
            let left = if *n_a == 0 { -1.0 } else { *ok_a as f64 / *n_a as f64 };
            let right = if *n_b == 0 { -1.0 } else { *ok_b as f64 / *n_b as f64 };
            left.partial_cmp(&right).unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| lane_b.cmp(lane_a))
        })
        .map(|(lane, _)| lane)
        .unwrap_or_else(|| "cloud".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha256_is_stable() {
        assert_eq!(
            sha256_hex(b"hello"),
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
    }

    #[test]
    fn resource_route_prefers_empirical_success() {
        let rows = vec![
            ResourceObservation { lane: "local".into(), task_class: "index".into(), duration: 1.0, memory_mb: 10, success: true },
            ResourceObservation { lane: "cloud".into(), task_class: "index".into(), duration: 1.0, memory_mb: 10, success: false },
        ];
        assert_eq!(route_resource("index", &rows, &["local".into(), "cloud".into()]), "local");
    }
}
