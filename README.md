# nauticuvs

[![crates.io](https://img.shields.io/crates/v/nauticuvs.svg)](https://crates.io/crates/nauticuvs) [![docs.rs](https://img.shields.io/docsrs/nauticuvs)](https://docs.rs/nauticuvs) [![license](https://img.shields.io/badge/license-MIT%20%2F%20Apache--2.0-green)](LICENSE)

**Nauticuvs — Fast Discrete Curvelet Transform (FDCT)** — a pure-Rust implementation for 2D image analysis.

Published on [crates.io](https://crates.io/crates/nauticuvs) as part of the CESARops ecosystem — supporting volunteer Search & Rescue (SAR) teams.

## What are curvelets?

Curvelets are a multi-scale, multi-directional frame designed to efficiently represent images with edges along smooth curves. They obey **parabolic scaling**:

> width ≈ length²

This makes them dramatically more efficient than wavelets for representing:
- **Side-scan sonar imagery** (seafloor features, shadows, edges)
- **Seismic data** (reflectors, fault lines)
- **Medical images** (tissue boundaries)
- **Any image with curvilinear singularities**

## Quick start

```rust
use ndarray::Array2;
use curvelet::{curvelet_forward, curvelet_inverse};

// Grayscale image as a 2D f32 array
let image = Array2::<f32>::zeros((512, 512));

// Forward transform (5 scales)
let coeffs = curvelet_forward(&image, 5).unwrap();

// Inverse transform — reconstruction is perfect (< 1e-6 relative error)
let reconstructed = curvelet_inverse(&coeffs).unwrap();
```

## Choosing the number of scales

The `num_scales` parameter (2–10) controls frequency octave decomposition:

| `num_scales` | Detail scales | Typical use |
|-------------|---------------|-------------|
| 3           | 1             | Small images (≤ 64), fast previews |
| 4–5         | 2–3           | General purpose, most applications |
| 6–8         | 4–6           | Large images (512+), fine-grained analysis |

**Rule of thumb:** `num_scales ≈ log₂(min(rows, cols)) - 2`

## Denoising

Curvelets are particularly effective for denoising. Noise spreads uniformly
across curvelet coefficients, while edges concentrate energy in a few large
coefficients:

```rust
let mut coeffs = curvelet_forward(&noisy_image, 5).unwrap();

// Hard thresholding — zero out small detail coefficients
coeffs.hard_threshold(0.1);

// Or soft thresholding — shrink magnitudes toward zero
// coeffs.soft_threshold(0.1);

let denoised = curvelet_inverse(&coeffs).unwrap();
```

**Threshold selection:** A common choice is the universal threshold `σ · √(2 · ln(N))`,
where `σ` is the noise standard deviation and `N` is the total number of pixels.

Both threshold methods only affect detail subbands — coarse (low-frequency)
and fine (high-frequency) scales are preserved.

## Image fusion

Combine two images by selecting the strongest curvelet coefficient at each position:

```rust
let coeffs_a = curvelet_forward(&image_a, 5).unwrap();
let mut fused = curvelet_forward(&image_b, 5).unwrap();

// Max-abs fusion: keep the stronger coefficient at each position
for (s, scale_a) in coeffs_a.detail.iter().enumerate() {
    for (d, dir_a) in scale_a.iter().enumerate() {
        for (target, source) in fused.detail[s][d].iter_mut().zip(dir_a.iter()) {
            if source.norm() > target.norm() {
                *target = *source;
            }
        }
    }
}

let fused_image = curvelet_inverse(&fused).unwrap();
```

## Custom configuration

Control the number of angular directions per scale:

```rust
use curvelet::{curvelet_forward_config, CurveletConfig};

// 64 directions at finest detail scale (default is 32)
let config = CurveletConfig::new(5).unwrap()
    .with_finest_directions(64).unwrap();

let coeffs = curvelet_forward_config(&image, &config).unwrap();
```

Or specify directions for every detail scale explicitly:

```rust
// 5 scales = 3 detail scales
let config = CurveletConfig::new(5).unwrap()
    .with_directions_per_scale(vec![8, 16, 32]).unwrap();
```

Direction counts must be ≥ 4 and multiples of 4.

## RGB / multi-channel images

The API operates on single-channel `Array2<f32>`. For RGB, transform each channel independently:

```rust
let mut coeffs_r = curvelet_forward(&red, 4).unwrap();
let mut coeffs_g = curvelet_forward(&green, 4).unwrap();
let mut coeffs_b = curvelet_forward(&blue, 4).unwrap();

// Denoise each channel
coeffs_r.hard_threshold(0.05);
coeffs_g.hard_threshold(0.05);
coeffs_b.hard_threshold(0.05);

let r = curvelet_inverse(&coeffs_r).unwrap();
let g = curvelet_inverse(&coeffs_g).unwrap();
let b = curvelet_inverse(&coeffs_b).unwrap();
```

## Coefficient structure

For `num_scales = 5`, the coefficient hierarchy is:

```
┌────────────────────────────────────────────┐
│ coarse   (1 isotropic subband, low-freq)   │  ← scale 0
├────────────────────────────────────────────┤
│ detail[0]  (16 directional subbands)       │  ← scale 1
│ detail[1]  (16 directional subbands)       │  ← scale 2
│ detail[2]  (32 directional subbands)       │  ← scale 3
├────────────────────────────────────────────┤
│ fine     (1 isotropic subband, high-freq)  │  ← scale 4
└────────────────────────────────────────────┘
```

Each subband is an `Array2<Complex<f64>>`. Accessing individual subbands:

```rust
let coeffs = curvelet_forward(&image, 5).unwrap();

// Coarse (low-frequency) subband
let coarse = &coeffs.coarse;

// Detail scale 1, direction 5
let subband = &coeffs.detail[1][5];

// Iterate all detail coefficients
for (scale_idx, scale) in coeffs.detail.iter().enumerate() {
    for (dir_idx, subband) in scale.iter().enumerate() {
        let energy: f64 = subband.iter().map(|c| c.norm_sqr()).sum();
        println!("scale {scale_idx}, dir {dir_idx}: energy = {energy:.2}");
    }
}

// Total coefficient count
println!("Total coefficients: {}", coeffs.num_coeffs());
```

## Parallelism

Enable the `parallel` feature to process directional subbands concurrently via rayon:

```toml
[dependencies]
curvelet = { version = "0.1", features = ["parallel"] }
```

This parallelizes:
- Window construction (per direction)
- Subband extraction in the forward transform
- Subband reconstruction in the inverse transform

## Precision

- **Public API:** accepts `Array2<f32>` images, returns `Array2<f32>`
- **Internal computation:** all FFTs, windows, and coefficients use `f64`
- **Reconstruction:** relative L2 error < 10⁻⁶ for unmodified coefficients, tested on 16×16 through 64×64 with 2–5 scales

## Error handling

All operations return `Result<T, CurveletError>`:

| Error | Cause |
|-------|-------|
| `ZeroDimension` | Image has 0 rows or columns |
| `NonFiniteInput` | Image contains NaN or Inf |
| `InvalidScaleCount` | `num_scales` not in [2, 10] |
| `InvalidDirectionCount` | Direction count < 4 or not a multiple of 4 |
| `DirectionCountMismatch` | Wrong number of entries in per-scale direction list |
| `InconsistentCoeffs` | Coefficient array dimensions don't match config |

## Dependencies

| Crate | Purpose |
|-------|---------|
| [`ndarray`](https://crates.io/crates/ndarray) | N-dimensional array operations |
| [`rustfft`](https://crates.io/crates/rustfft) | FFT implementation |
| [`num-complex`](https://crates.io/crates/num-complex) | Complex number arithmetic |
| [`thiserror`](https://crates.io/crates/thiserror) | Error types |
| [`rayon`](https://crates.io/crates/rayon) | Optional parallelization (`parallel` feature) |

## Project Philosophy

This crate is a core component of the [CESARops](https://cesarops.com/) (Civilian Emergency Search and Rescue Operations Platform) and [Sonar Sniffer](https://cesarops.com/) projects. Our mission is to provide powerful, professional-grade tools to Search and Rescue (SAR) teams at no cost.

-   **Free for SAR:** This library, along with Sonar Sniffer and the CESARops platform, will always be free for civilian and volunteer SAR organizations.
-   **Community Driven:** We believe in open-source, privacy-first software built by the SAR community, for the SAR community.
-   **Sustainable Development:** To fund further development, we may offer this software under a commercial license to the public or for-profit entities. The revenue generated will support the maintenance and enhancement of the free tools available to SAR teams.

This `curvelet` crate provides the signal processing foundation for advanced sonar and image analysis, which is critical for features like drift prediction and object detection in challenging environments.

## Contributing

We are actively looking for contributors to help finish and expand the CESARops ecosystem. Whether you are a developer, a designer, a SAR professional, or just someone with a passion for helping others, there are many ways to get involved.

Please see our main repository for contribution guidelines and to learn more about how you can help.

## Sponsors / Donate

`nauticuvs` and the CESARops ecosystem are free for volunteer SAR teams, forever. If you'd like to help keep the lights on, any contribution is appreciated.

| Platform | Link |
|----------|------|
| Cash App | [$nautidogsailing](https://cash.app/$nautidogsailing) |
| GitHub Sponsors | [github.com/sponsors/festeraeb](https://github.com/sponsors/festeraeb) |

## References

- Candès, E.J., Demanet, L., Donoho, D.L., Ying, L. (2006). "Fast Discrete Curvelet Transforms." *Multiscale Modeling & Simulation*, 5(3), 861–899.
- Candès, E.J. & Donoho, D.L. (2004). "New tight frames of curvelets and optimal representations of objects with piecewise C² singularities." *Comm. Pure Appl. Math.*, 57(2), 219–266.

## License

MIT OR Apache-2.0
