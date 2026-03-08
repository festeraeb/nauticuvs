//! Frequency-domain window functions for the curvelet transform.
//!
//! The FDCT uses two types of windows:
//!
//! 1. **Radial windows** `W_j(r)` — select the annular frequency band for
//!    each scale `j`. Adjacent windows overlap and satisfy:
//!    `|W_j(r)|² + |W_{j+1}(r)|² = 1` in every transition zone.
//!
//! 2. **Angular windows** `V_l(θ)` — select directional wedges within each
//!    annular band. Adjacent windows overlap and satisfy:
//!    `|V_l(θ)|² + |V_{l+1}(θ)|² = 1` in every transition zone.
//!
//! Together these guarantee a tight-frame partition of unity:
//! `Σ_{j,l} |W_j(r) · V_l(θ)|² = 1` for all `(r, θ)`.

use std::f64::consts::PI;

use ndarray::Array2;

/// Smooth Meyer auxiliary function `ν(t)`.
///
/// Satisfies `ν(t) + ν(1-t) = 1` for `t ∈ [0,1]`, enabling the
/// construction of smooth partitions of unity.
///
/// Uses the polynomial `ν(t) = t⁴(35 - 84t + 70t² - 20t³)`,
/// which is C³ and flat at endpoints.
#[inline]
pub fn meyer_nu(t: f64) -> f64 {
    if t <= 0.0 {
        return 0.0;
    }
    if t >= 1.0 {
        return 1.0;
    }
    let t2 = t * t;
    let t3 = t2 * t;
    let t4 = t3 * t;
    t4 * (35.0 - 84.0 * t + 70.0 * t2 - 20.0 * t3)
}

/// Compute the radial frequency band boundaries for `num_scales` scales.
///
/// Returns `num_scales + 1` boundary values in `[0, 0.5]` with geometric
/// (octave) spacing. For `num_scales = 5`:
///
/// ```text
/// [0, 0.03125, 0.0625, 0.125, 0.25, 0.5]
/// ```
///
/// Transitions between adjacent scales happen in the interval
/// `[boundary[j], boundary[j+1]]`.
pub fn scale_boundaries(num_scales: usize) -> Vec<f64> {
    let mut boundaries = Vec::with_capacity(num_scales + 1);
    boundaries.push(0.0);
    for j in 0..num_scales {
        let exp = (num_scales - 1 - j) as f64;
        boundaries.push(0.5 * 2.0f64.powf(-exp));
    }
    boundaries
}

/// Build the radial window for scale `scale_idx` on an `n × n` grid.
///
/// The windows are designed so that in every transition zone
/// `[boundary[j], boundary[j+1]]`, the sum of squared adjacent windows
/// equals 1:
///
/// ```text
/// |W_j(r)|² + |W_{j+1}(r)|² = 1
/// ```
///
/// This is achieved by using complementary `ν(t)` and `1 - ν(t)` ramps.
///
/// ## Scale layout
///
/// - **Coarse (j=0)**: low-pass, = 1 for `r ≤ b[1]`, falls in `[b[1], b[2]]`
/// - **Detail (j=1..J-2)**: bandpass, rises in `[b[j], b[j+1]]`, falls in `[b[j+1], b[j+2]]`
/// - **Fine (j=J-1)**: high-pass, rises in `[b[J-1], b[J]]`, = 1 for `r ≥ b[J]`
pub fn build_radial_window(
    radial: &Array2<f64>,
    scale_idx: usize,
    num_scales: usize,
) -> Array2<f64> {
    let bounds = scale_boundaries(num_scales);
    let n = radial.nrows();
    let mut w = Array2::zeros((n, n));

    if scale_idx == 0 {
        // Low-pass: 1 for r ≤ b[1], transition in [b[1], b[2]]
        let b1 = bounds[1];
        let b2 = bounds[2].min(0.5); // safety
        for i in 0..n {
            for j in 0..n {
                let r = radial[[i, j]];
                if r <= b1 {
                    w[[i, j]] = 1.0;
                } else if r < b2 {
                    let t = (r - b1) / (b2 - b1);
                    w[[i, j]] = (1.0 - meyer_nu(t)).sqrt();
                }
            }
        }
        return w;
    }

    if scale_idx == num_scales - 1 {
        // High-pass: rises in [b[J-1], b[J]], 1 for r ≥ b[J]
        let bj_1 = bounds[num_scales - 1];
        let bj = bounds[num_scales];
        for i in 0..n {
            for j in 0..n {
                let r = radial[[i, j]];
                if r >= bj {
                    w[[i, j]] = 1.0;
                } else if r > bj_1 {
                    let t = (r - bj_1) / (bj - bj_1);
                    w[[i, j]] = meyer_nu(t).sqrt();
                }
            }
        }
        return w;
    }

    // Detail band-pass: rises in [b[j], b[j+1]], falls in [b[j+1], b[j+2]]
    let b_lo = bounds[scale_idx];
    let b_mid = bounds[scale_idx + 1];
    let b_hi = bounds[scale_idx + 2];

    for i in 0..n {
        for j in 0..n {
            let r = radial[[i, j]];
            if r <= b_lo || r >= b_hi {
                continue;
            }
            w[[i, j]] = if r <= b_mid {
                let t = (r - b_lo) / (b_mid - b_lo);
                meyer_nu(t).sqrt()
            } else {
                let t = (r - b_mid) / (b_hi - b_mid);
                (1.0 - meyer_nu(t)).sqrt()
            };
        }
    }
    w
}

/// Build a smooth angular window for direction `dir_idx` out of `num_dirs`.
///
/// Each window is a bell-shaped bump centered at its sector angle, with
/// support spanning one full sector width on each side. Adjacent windows
/// overlap and satisfy:
///
/// ```text
/// |V_l(θ)|² + |V_{l+1}(θ)|² = 1
/// ```
///
/// in the transition zone between adjacent sector centers.
pub fn build_angular_window(theta: &Array2<f64>, dir_idx: usize, num_dirs: usize) -> Array2<f64> {
    let n = theta.nrows();
    let sector_width = 2.0 * PI / num_dirs as f64;
    let center = -PI + (dir_idx as f64 + 0.5) * sector_width;

    let mut w = Array2::zeros((n, n));
    for i in 0..n {
        for j in 0..n {
            // Signed angular distance, wrapped to [-π, π]
            let mut dtheta = theta[[i, j]] - center;
            dtheta = dtheta.rem_euclid(2.0 * PI);
            if dtheta > PI {
                dtheta -= 2.0 * PI;
            }

            let abs_dt = dtheta.abs();
            if abs_dt < sector_width {
                let t = abs_dt / sector_width;
                w[[i, j]] = (1.0 - meyer_nu(t)).sqrt();
            }
        }
    }
    w
}

/// Build the combined radial × angular window for one subband.
pub fn build_combined_window(
    rad_w: &Array2<f64>,
    theta: &Array2<f64>,
    dir_idx: usize,
    num_dirs: usize,
) -> Array2<f64> {
    let ang_w = build_angular_window(theta, dir_idx, num_dirs);

    let n = rad_w.nrows();
    let mut combined = Array2::zeros((n, n));
    for i in 0..n {
        for j in 0..n {
            combined[[i, j]] = rad_w[[i, j]] * ang_w[[i, j]];
        }
    }
    combined
}

#[cfg(test)]
mod window_tests {
    use super::*;

    #[test]
    fn test_meyer_nu_endpoints() {
        assert!((meyer_nu(0.0) - 0.0).abs() < 1e-14);
        assert!((meyer_nu(1.0) - 1.0).abs() < 1e-14);
    }

    #[test]
    fn test_meyer_nu_symmetry() {
        // ν(t) + ν(1-t) = 1
        for i in 0..=100 {
            let t = i as f64 / 100.0;
            let sum = meyer_nu(t) + meyer_nu(1.0 - t);
            assert!(
                (sum - 1.0).abs() < 1e-12,
                "ν({t}) + ν({}) = {sum}, expected 1.0",
                1.0 - t
            );
        }
    }

    #[test]
    fn test_scale_boundaries() {
        let b = scale_boundaries(5);
        assert_eq!(b.len(), 6);
        assert!((b[0] - 0.0).abs() < 1e-14);
        assert!((b[5] - 0.5).abs() < 1e-14);
        for i in 1..b.len() {
            assert!(b[i] > b[i - 1]);
        }
    }

    #[test]
    fn test_radial_pou() {
        // Verify that the sum of squared radial windows = 1 everywhere
        let num_scales = 5;
        let n = 64;
        let (xi_row, xi_col) = crate::utils::freq_grid_2d_f64(n);
        let radial = crate::utils::radial_freq_f64(&xi_row, &xi_col);

        let mut pou = Array2::<f64>::zeros((n, n));
        for s in 0..num_scales {
            let w = build_radial_window(&radial, s, num_scales);
            for i in 0..n {
                for j in 0..n {
                    pou[[i, j]] += w[[i, j]] * w[[i, j]];
                }
            }
        }

        // Check POU ≈ 1 everywhere except possibly at r=0 (DC)
        let mut max_deviation = 0.0f64;
        for i in 0..n {
            for j in 0..n {
                if radial[[i, j]] > 1e-10 {
                    let dev = (pou[[i, j]] - 1.0).abs();
                    if dev > max_deviation {
                        max_deviation = dev;
                    }
                }
            }
        }
        assert!(
            max_deviation < 1e-10,
            "Radial POU max deviation: {max_deviation}"
        );
    }

    #[test]
    fn test_angular_pou() {
        // Verify that the sum of squared angular windows = 1
        let num_dirs = 16;
        let n = 64;
        let (xi_row, xi_col) = crate::utils::freq_grid_2d_f64(n);
        let theta = crate::utils::angular_freq_f64(&xi_row, &xi_col);

        let mut pou = Array2::<f64>::zeros((n, n));
        for l in 0..num_dirs {
            let w = build_angular_window(&theta, l, num_dirs);
            for i in 0..n {
                for j in 0..n {
                    pou[[i, j]] += w[[i, j]] * w[[i, j]];
                }
            }
        }

        // Check POU ≈ 1 everywhere except at origin (θ undefined)
        let mut max_deviation = 0.0f64;
        for i in 0..n {
            for j in 0..n {
                // Skip origin where angle is undefined
                if i == 0 && j == 0 {
                    continue;
                }
                let dev = (pou[[i, j]] - 1.0).abs();
                if dev > max_deviation {
                    max_deviation = dev;
                }
            }
        }
        assert!(
            max_deviation < 1e-10,
            "Angular POU max deviation: {max_deviation}"
        );
    }
}
