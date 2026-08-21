//! Utility functions: padding, frequency grids, power-of-2 helpers.

use ndarray::{s, Array2};

use crate::error::CurveletError;

/// Pad a 2D f32 array to `(rows, cols)` with zeros.
pub fn zero_pad(image: &Array2<f32>, rows: usize, cols: usize) -> Array2<f64> {
    let (orig_r, orig_c) = image.dim();
    let mut padded = Array2::zeros((rows, cols));
    padded
        .slice_mut(s![..orig_r, ..orig_c])
        .assign(&image.mapv(|v| v as f64));
    padded
}

/// Crop a 2D f64 array back to `(rows, cols)` and convert to f32.
pub fn crop_to_f32(image: &Array2<f64>, rows: usize, cols: usize) -> Array2<f32> {
    image.slice(s![..rows, ..cols]).mapv(|v| v as f32)
}

/// Validate that the image has non-zero dimensions and finite values.
pub fn validate_image(image: &Array2<f32>) -> Result<(), CurveletError> {
    let (rows, cols) = image.dim();
    if rows == 0 || cols == 0 {
        return Err(CurveletError::ZeroDimension { rows, cols });
    }
    if !image.iter().all(|v| v.is_finite()) {
        return Err(CurveletError::NonFiniteInput);
    }
    Ok(())
}

/// Generate centered frequency coordinates for one dimension of size `n` (f64).
pub fn freq_grid_1d_f64(n: usize) -> Vec<f64> {
    let nf = n as f64;
    (0..n)
        .map(|i| {
            if i <= n / 2 {
                i as f64 / nf
            } else {
                (i as f64 - nf) / nf
            }
        })
        .collect()
}

/// Generate 2D frequency grids (ξ_row, ξ_col) for an `n × n` FFT (f64).
pub fn freq_grid_2d_f64(n: usize) -> (Array2<f64>, Array2<f64>) {
    let f1d = freq_grid_1d_f64(n);
    let xi_row = Array2::from_shape_fn((n, n), |(r, _)| f1d[r]);
    let xi_col = Array2::from_shape_fn((n, n), |(_, c)| f1d[c]);
    (xi_row, xi_col)
}

/// Compute the radial frequency |ξ| = sqrt(ξ_row² + ξ_col²) (f64).
pub fn radial_freq_f64(xi_row: &Array2<f64>, xi_col: &Array2<f64>) -> Array2<f64> {
    let n = xi_row.nrows();
    let mut r = Array2::zeros((n, n));
    for i in 0..n {
        for j in 0..n {
            r[[i, j]] = (xi_row[[i, j]].powi(2) + xi_col[[i, j]].powi(2)).sqrt();
        }
    }
    r
}

/// Compute the angular frequency atan2(ξ_row, ξ_col) (f64).
pub fn angular_freq_f64(xi_row: &Array2<f64>, xi_col: &Array2<f64>) -> Array2<f64> {
    let n = xi_row.nrows();
    let mut theta = Array2::zeros((n, n));
    for i in 0..n {
        for j in 0..n {
            theta[[i, j]] = xi_row[[i, j]].atan2(xi_col[[i, j]]);
        }
    }
    theta
}
