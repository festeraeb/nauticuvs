//! Wrapping (periodization) for curvelet subbands.
//!
//! This module provides wrapping utilities for a future memory-optimized path.
//! The current transform uses full-size (n×n) subbands for perfect reconstruction;
//! wrapping can be re-added as an optimization once memory becomes a concern.
#![allow(dead_code)]
//!
//! The wrapping is conceptually:
//!
//! ```text
//! wrapped[r mod M, c mod N] += input[r, c]
//! ```
//!
//! where `(M, N)` is the size of the wrapped rectangle, chosen to be the
//! smallest rectangle containing the essential support of the curvelet window.

use ndarray::Array2;
use num_complex::Complex;

/// Compute the wrapping rectangle size for a given scale and the padded image size.
///
/// The rectangle dimensions are chosen to tightly contain the essential support
/// of the curvelet window at the given scale. For the FDCT via wrapping:
///
/// - At scale `j`, the radial band has width ~ `2^j` bins
/// - The angular aperture gives a length that follows parabolic scaling
///
/// We use a simple heuristic: the wrapped rectangle is
/// `(n / 2^(num_scales - 1 - scale_idx), n / 2^(floor((num_scales - 1 - scale_idx)/2)))`.
///
/// This ensures:
/// - Finer scales → larger rectangles (more coefficients)
/// - Width grows slower than length (parabolic scaling)
pub fn wrapping_rectangle_size(scale_idx: usize, num_scales: usize, n: usize) -> (usize, usize) {
    // For the coarse and fine isotropic scales, the rectangle is the same
    // as the effective support.
    if scale_idx == 0 {
        // Coarse: small square capturing low frequencies
        let size = (n >> (num_scales - 2)).max(4);
        return (size, size);
    }
    if scale_idx == num_scales - 1 {
        // Fine: full resolution
        return (n, n);
    }

    // Detail scales: use parabolic scaling.
    // The "level" from finest detail: 0 = finest detail, increasing = coarser.
    let detail_idx = scale_idx - 1;
    let num_detail = num_scales - 2;
    let level_from_finest = num_detail - 1 - detail_idx;

    // Length (along the curve direction) shrinks slowly: n / 2^(level/2)
    // Width (perpendicular to curve) shrinks fast: n / 2^level
    let length_shift = level_from_finest / 2;
    let width_shift = level_from_finest;

    let length = (n >> length_shift).max(4);
    let width = (n >> width_shift).max(4);

    (length, width)
}

/// Wrap (periodize) a full-size `n × n` complex array into an `(M, N)` rectangle.
///
/// This performs modular accumulation:
/// ```text
/// output[r % M, c % N] += input[r, c]
/// ```
pub fn wrap_to_rectangle(
    input: &Array2<Complex<f32>>,
    target_rows: usize,
    target_cols: usize,
) -> Array2<Complex<f32>> {
    let (in_rows, in_cols) = input.dim();
    let mut output = Array2::zeros((target_rows, target_cols));

    for r in 0..in_rows {
        for c in 0..in_cols {
            let tr = r % target_rows;
            let tc = c % target_cols;
            output[[tr, tc]] += input[[r, c]];
        }
    }
    output
}

/// Unwrap a small `(M, N)` rectangle back to full `n × n` size.
///
/// This is the adjoint of wrapping: the small rectangle is tiled to fill the
/// full `n × n` grid. Each output pixel gets the value from the corresponding
/// position in the wrapped rectangle.
pub fn unwrap_from_rectangle(
    wrapped: &Array2<Complex<f32>>,
    n_rows: usize,
    n_cols: usize,
) -> Array2<Complex<f32>> {
    let (wr, wc) = wrapped.dim();
    let mut output = Array2::zeros((n_rows, n_cols));

    for r in 0..n_rows {
        for c in 0..n_cols {
            output[[r, c]] = wrapped[[r % wr, c % wc]];
        }
    }
    output
}

#[cfg(test)]
mod wrapping_tests {
    use super::*;
    use num_complex::Complex;

    #[test]
    fn test_wrap_unwrap_identity_when_same_size() {
        let n = 16;
        let data: Array2<Complex<f32>> =
            Array2::from_shape_fn((n, n), |(r, c)| Complex::new((r * n + c) as f32, 0.0));
        let wrapped = wrap_to_rectangle(&data, n, n);
        assert_eq!(data, wrapped);
    }

    #[test]
    fn test_wrap_accumulation() {
        let n = 8;
        let ones = Array2::from_elem((n, n), Complex::new(1.0f32, 0.0));
        let wrapped = wrap_to_rectangle(&ones, 4, 4);
        // Each element in 4×4 should accumulate 4 values (8/4 * 8/4 = 4)
        for &v in wrapped.iter() {
            assert!((v.re - 4.0).abs() < 1e-10);
        }
    }

    #[test]
    fn test_rectangle_sizes_monotonic() {
        let n = 256;
        let num_scales = 5;
        let mut prev_area = 0;
        for s in 0..num_scales {
            let (rows, cols) = wrapping_rectangle_size(s, num_scales, n);
            let area = rows * cols;
            assert!(
                area >= prev_area,
                "scale {s} area {area} < previous {prev_area}"
            );
            prev_area = area;
        }
    }
}
