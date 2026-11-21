use serde::Serialize;
use std::os::windows::process::CommandExt;
use std::process::Command;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Serialize, Debug, PartialEq)]
pub struct GpuStats {
    pub name: String,
    pub utilization_pct: Option<u32>,
    pub memory_used_mb: Option<u64>,
    pub memory_total_mb: Option<u64>,
    pub temperature_c: Option<u32>,
    pub power_w: Option<f64>,
}

pub fn parse_gpu_line(line: &str) -> Option<GpuStats> {
    let fields: Vec<&str> = line.split(',').map(str::trim).collect();
    if fields.len() < 6 {
        return None;
    }
    let tail = &fields[fields.len() - 5..];
    let name = fields[..fields.len() - 5].join(", ");
    if name.is_empty() {
        return None;
    }
    Some(GpuStats {
        name,
        utilization_pct: tail[0].parse().ok(),
        memory_used_mb: tail[1].parse().ok(),
        memory_total_mb: tail[2].parse().ok(),
        temperature_c: tail[3].parse().ok(),
        power_w: tail[4].parse().ok(),
    })
}

#[tauri::command(async)]
pub fn gpu_stats() -> Option<GpuStats> {
    let out = Command::new("nvidia-smi")
        .args([
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    text.lines().find_map(parse_gpu_line)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_full_line() {
        let g = parse_gpu_line("NVIDIA GeForce RTX 5070 Ti, 37, 10432, 16303, 58, 142.35").unwrap();
        assert_eq!(g.name, "NVIDIA GeForce RTX 5070 Ti");
        assert_eq!(g.utilization_pct, Some(37));
        assert_eq!(g.memory_used_mb, Some(10432));
        assert_eq!(g.memory_total_mb, Some(16303));
        assert_eq!(g.temperature_c, Some(58));
        assert_eq!(g.power_w, Some(142.35));
    }

    #[test]
    fn unavailable_fields_become_none() {
        let g = parse_gpu_line("NVIDIA GeForce RTX 5070 Ti, 0, 512, 16303, [N/A], [N/A]").unwrap();
        assert_eq!(g.utilization_pct, Some(0));
        assert_eq!(g.temperature_c, None);
        assert_eq!(g.power_w, None);
        let g = parse_gpu_line("Tesla T4, [N/A], [N/A], [N/A], [N/A], [N/A]").unwrap();
        assert_eq!(g.name, "Tesla T4");
        assert_eq!(g.utilization_pct, None);
        assert_eq!(g.memory_total_mb, None);
    }

    #[test]
    fn name_with_commas_is_kept_whole() {
        let g = parse_gpu_line("Foo, Bar Edition, 5, 100, 200, 40, 20.5").unwrap();
        assert_eq!(g.name, "Foo, Bar Edition");
        assert_eq!(g.power_w, Some(20.5));
    }

    #[test]
    fn rejects_junk() {
        assert!(parse_gpu_line("").is_none());
        assert!(parse_gpu_line("NVIDIA-SMI has failed because it couldn't communicate").is_none());
    }
}
