//! Inverse curvelet transform (synthesis).
//!
//! Reconstructs a 2D image from curvelet coefficients:
//!
//! 1. For each subband: FFT the spatial-domain coefficients
//! 2. Multiply by the same normalized window used in the forward pass
//! 3. Sum all contributions in the frequency domain
//! 4. Inverse FFT → spatial domain (f64)
//! 5. Crop to original dimensions and convert to f32
//!
//! With the `parallel` feature, directional subbands are processed
//! concurrently and their contributions summed via parallel reduce.

use ndarray::Array2;
use num_complex::Complex;

#[cfg(feature = "parallel")]
use rayon::prelude::*;

use crate::coeffs::CurveletCoeffs;
use crate::error::CurveletError;
use crate::fft;
use crate::utils;
use crate::windows;

/// Compute a single subband's frequency-domain contribution:
/// FFT(coefficients) × window × (1/sqrt(POU)).
fn subband_contribution(
    coeffs: &Array2<Complex<f64>>,
    w_arr: &Array2<f64>,
    inv_sqrt_pou: &Array2<f64>,
    n: usize,
) -> Array2<Complex<f64>> {
    let mut freq = coeffs.clone();
    fft::fft2_inplace(&mut freq);
    let mut contrib = Array2::zeros((n, n));
    for i in 0..n {
        for j in 0..n {
            let w = w_arr[[i, j]] * inv_sqrt_pou[[i, j]];
            contrib[[i, j]] = freq[[i, j]] * w;
        }
    }
    contrib
}

/// Compute the inverse curvelet transform (reconstruction).
pub fn inverse_transform(coeffs: &CurveletCoeffs) -> Result<Array2<f32>, CurveletError> {
    coeffs.validate()?;

    let cfg = &coeffs.config;
    let n = cfg.padded_size;
    let num_scales = cfg.num_scales;

    if n == 0 {
        return Err(CurveletError::InconsistentCoeffs(
            "padded_size is 0 (coefficients not from a forward transform?)".to_string(),
        ));
    }

    // Precompute frequency grids (must match forward exactly)
    let (xi_row, xi_col) = utils::freq_grid_2d_f64(n);
    let radial = utils::radial_freq_f64(&xi_row, &xi_col);
    let theta = utils::angular_freq_f64(&xi_row, &xi_col);

    // Rebuild windows and POU
    let coarse_window = windows::build_radial_window(&radial, 0, num_scales);
    let fine_window = windows::build_radial_window(&radial, num_scales - 1, num_scales);

    let mut pou = Array2::<f64>::zeros((n, n));
    for i in 0..n {
        for j in 0..n {
            pou[[i, j]] += coarse_window[[i, j]].powi(2);
            pou[[i, j]] += fine_window[[i, j]].powi(2);
        }
    }

    let num_detail = cfg.num_detail_scales();
    let mut detail_windows: Vec<Vec<Array2<f64>>> = Vec::with_capacity(num_detail);

    for d in 0..num_detail {
        let scale_idx = d + 1;
        let num_dirs = cfg.directions_at_detail_scale(d);
        let rad_w = windows::build_radial_window(&radial, scale_idx, num_scales);

        #[cfg(feature = "parallel")]
        let dirs_windows: Vec<Array2<f64>> = (0..num_dirs)
            .into_par_iter()
            .map(|l| windows::build_combined_window(&rad_w, &theta, l, num_dirs))
            .collect();

        #[cfg(not(feature = "parallel"))]
        let dirs_windows: Vec<Array2<f64>> = (0..num_dirs)
            .map(|l| windows::build_combined_window(&rad_w, &theta, l, num_dirs))
            .collect();

        for w in &dirs_windows {
            for i in 0..n {
                for j in 0..n {
                    pou[[i, j]] += w[[i, j]].powi(2);
                }
            }
        }
        detail_windows.push(dirs_windows);
    }

    pou.mapv_inplace(|v| if v < 1e-30 { 1.0 } else { v });

    let inv_sqrt_pou = pou.mapv(|v| 1.0 / v.sqrt());

    // Accumulate reconstructed spectrum
    let mut f_hat = subband_contribution(&coeffs.coarse, &coarse_window, &inv_sqrt_pou, n);

    // Detail contributions (parallel over directions, then sum)
    for (scale_windows, scale_coeffs) in detail_windows.iter().zip(coeffs.detail.iter()) {
        #[cfg(feature = "parallel")]
        {
            let contributions: Vec<Array2<Complex<f64>>> = scale_windows
                .par_iter()
                .zip(scale_coeffs.par_iter())
                .map(|(w_arr, dir_coeffs)| {
                    subband_contribution(dir_coeffs, w_arr, &inv_sqrt_pou, n)
                })
                .collect();
            for contrib in contributions {
                for i in 0..n {
                    for j in 0..n {
                        f_hat[[i, j]] += contrib[[i, j]];
                    }
                }
            }
        }

        #[cfg(not(feature = "parallel"))]
        {
            for (w_arr, dir_coeffs) in scale_windows.iter().zip(scale_coeffs.iter()) {
                let contrib = subband_contribution(dir_coeffs, w_arr, &inv_sqrt_pou, n);
                for i in 0..n {
                    for j in 0..n {
                        f_hat[[i, j]] += contrib[[i, j]];
                    }
                }
            }
        }
    }

    // Fine contribution
    let fine_contrib = subband_contribution(&coeffs.fine, &fine_window, &inv_sqrt_pou, n);
    for i in 0..n {
        for j in 0..n {
            f_hat[[i, j]] += fine_contrib[[i, j]];
        }
    }

    // Inverse FFT → spatial domain
    fft::ifft2_inplace(&mut f_hat);
    let reconstructed = fft::complex_to_real(&f_hat);

    Ok(utils::crop_to_f32(
        &reconstructed,
        cfg.original_rows,
        cfg.original_cols,
    ))
}
