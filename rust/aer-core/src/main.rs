use aer_core::{route_resource, sha256_hex, Request, Response};
use std::io::{self, BufRead, Write};

fn main() {
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(value) if !value.trim().is_empty() => value,
            _ => continue,
        };
        let result = serde_json::from_str::<Request>(&line).map(|request| match request {
            Request::Sha256 { value } => Response {
                ok: true,
                op: "sha256".into(),
                value: serde_json::json!({"sha256": sha256_hex(value.as_bytes())}),
            },
            Request::ResourceRoute { task_class, observations, lanes } => Response {
                ok: true,
                op: "resource_route".into(),
                value: serde_json::json!({"lane": route_resource(&task_class, &observations, &lanes)}),
            },
        });
        let response = match result {
            Ok(value) => value,
            Err(error) => Response {
                ok: false,
                op: "error".into(),
                value: serde_json::json!({"error": error.to_string()}),
            },
        };
        let _ = writeln!(stdout, "{}", serde_json::to_string(&response).unwrap());
        let _ = stdout.flush();
    }
}
