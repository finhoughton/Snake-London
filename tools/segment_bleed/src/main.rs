//! Rasterise segment highlights and report where the probe colour lands.
//!
//! Python builds the SVGs (render.py stays the source of truth for what a highlight looks
//! like); this does the expensive part: render in memory, scan for the probe colour, drop
//! the two claimed station markers. No PNG round trip and no process per segment.
//!
//! stdin:  JSON { scale, tolerance, probe: [r,g,b], jobs: [{ svg, x0, y0, w, h, cuts }] }
//! stdout: for each job in order, u32 point count then that many (i32 x, i32 y) pairs.

use rayon::prelude::*;
use resvg::{tiny_skia, usvg};
use serde::Deserialize;
use std::io::{Read, Write};

#[derive(Deserialize)]
struct Job {
    svg: String,
    x0: i32,
    y0: i32,
    w: u32,
    h: u32,
    cuts: Vec<(f64, f64, f64)>,
}

#[derive(Deserialize)]
struct Input {
    scale: f64,
    tolerance: i32,
    probe: (i32, i32, i32),
    jobs: Vec<Job>,
}

fn scan(job: &Job, scale: f64, probe: (i32, i32, i32), tolerance: i32) -> Vec<(i32, i32)> {
    let tree = usvg::Tree::from_str(&job.svg, &usvg::Options::default()).expect("parse svg");
    let width = ((job.w as f64) * scale).round().max(1.0) as u32;
    let height = ((job.h as f64) * scale).round().max(1.0) as u32;
    let mut pixmap = tiny_skia::Pixmap::new(width, height).expect("pixmap");
    resvg::render(
        &tree,
        tiny_skia::Transform::from_scale(scale as f32, scale as f32),
        &mut pixmap.as_mut(),
    );

    let (pr, pg, pb) = probe;
    let limit = tolerance * tolerance;
    let step = (1.0 / scale).round() as i32;
    let mut points = Vec::new();
    for (index, pixel) in pixmap.pixels().iter().enumerate() {
        let colour = pixel.demultiply();
        let (dr, dg, db) = (
            colour.red() as i32 - pr,
            colour.green() as i32 - pg,
            colour.blue() as i32 - pb,
        );
        if dr * dr + dg * dg + db * db >= limit {
            continue;
        }
        let x = job.x0 + (index as u32 % width) as i32 * step;
        let y = job.y0 + (index as u32 / width) as i32 * step;
        if job
            .cuts
            .iter()
            .any(|(cx, cy, r)| ((x as f64 - cx).powi(2) + (y as f64 - cy).powi(2)).sqrt() <= *r)
        {
            continue;
        }
        points.push((x, y));
    }
    points
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("read stdin");
    let input: Input = serde_json::from_str(&raw).expect("parse input");

    let results: Vec<Vec<(i32, i32)>> = input
        .jobs
        .par_iter()
        .map(|job| scan(job, input.scale, input.probe, input.tolerance))
        .collect();

    let mut out = Vec::with_capacity(results.iter().map(|r| r.len() * 8 + 4).sum());
    for points in &results {
        out.extend_from_slice(&(points.len() as u32).to_le_bytes());
        for (x, y) in points {
            out.extend_from_slice(&x.to_le_bytes());
            out.extend_from_slice(&y.to_le_bytes());
        }
    }
    std::io::stdout().write_all(&out).expect("write stdout");
}
