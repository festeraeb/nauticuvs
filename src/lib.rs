//! # Fast Discrete Curvelet Transform (FDCT)
//!
//! A pure-Rust implementation of the Fast Discrete Curvelet Transform
//! based on the algorithm of Candès, Demanet, Donoho & Ying (2006).
//!
//! ## What are curvelets?
//!
//! Curvelets are a multi-scale, multi-directional frame that provides
//! near-optimal sparse representations of images with smooth edges along
//! curves — making them ideal for sonar imagery, seismic data, and
//! medical imaging.
//!
//! Unlike wavelets (which capture point singularities well), curvelets
//! capture **curve singularities** with far fewer coefficients. At scale
//! `2^{-j}`, curvelets have width ~ `2^{-j}` and length ~ `2^{-j/2}`,
//! obeying the **parabolic scaling** relation: *width ≈ length²*.
//!
//! ## Quick start
//!
//! ```rust
//! use ndarray::Array2;
//! use nauticuvs::{curvelet_forward, curvelet_inverse};
//!
//! let image = Array2::<f32>::zeros((256, 256));
//! let coeffs = curvelet_forward(&image, 5).unwrap();
//! let reconstructed = curvelet_inverse(&coeffs).unwrap();
//! ```
//!
//! ## Choosing the number of scales
//!
//! The `num_scales` parameter (2–10) controls how many frequency octaves
//! the decomposition produces:
//!
//! | `num_scales` | Detail scales | Typical use |
//! |-------------|---------------|-------------|
//! | 3           | 1             | Small images (≤ 64), fast previews |
//! | 4–5         | 2–3           | General purpose, most applications |
//! | 6–8         | 4–6           | Large images (512+), fine-grained analysis |
//!
//! A good rule of thumb: `num_scales ≈ log₂(min(rows, cols)) - 2`.
//!
//! ## Denoising
//!
//! Curvelets are particularly effective for denoising. Noise spreads
//! uniformly across curvelet coefficients, while edges concentrate
//! energy in a few large coefficients. Thresholding removes noise
//! while preserving edges:
//!
//! ```rust
//! # use ndarray::Array2;
//! # use nauticuvs::{curvelet_forward, curvelet_inverse};
//! let noisy_image = Array2::<f32>::zeros((128, 128));
//! let mut coeffs = curvelet_forward(&noisy_image, 4).unwrap();
//!
//! // Hard thresholding: zero out small coefficients
//! coeffs.hard_threshold(0.1);
//!
//! // Or soft thresholding: shrink magnitudes toward zero
//! // coeffs.soft_threshold(0.1);
//!
//! let denoised = curvelet_inverse(&coeffs).unwrap();
//! ```
//!
//! **Threshold selection:** A common choice is the universal threshold
//! `σ · √(2 · ln(N))`, where `σ` is the noise standard deviation and
//! `N` is the total number of pixels.
//!
//! ## Custom configuration
//!
//! The default uses 32 directions at the finest detail scale, halving
//! every two scales. You can override this:
//!
//! ```rust
//! # use ndarray::Array2;
//! use nauticuvs::{curvelet_forward_config, curvelet_inverse, CurveletConfig};
//!
//! let config = CurveletConfig::new(5).unwrap()
//!     .with_finest_directions(64).unwrap();   // 64 directions at finest
//!
//! let image = Array2::<f32>::zeros((256, 256));
//! let coeffs = curvelet_forward_config(&image, &config).unwrap();
//! let reconstructed = curvelet_inverse(&coeffs).unwrap();
//! ```
//!
//! Or specify directions for every detail scale explicitly:
//!
//! ```rust
//! # use nauticuvs::CurveletConfig;
//! let config = CurveletConfig::new(5).unwrap()
//!     .with_directions_per_scale(vec![8, 16, 32]).unwrap();
//! // 3 detail scales: 8, 16, 32 directions respectively
//! ```
//!
//! ## Image fusion
//!
//! Combine two images by selecting the strongest curvelet coefficient
//! at each position (max-abs fusion rule):
//!
//! ```rust
//! # use ndarray::Array2;
//! # use nauticuvs::{curvelet_forward, curvelet_inverse};
//! let image_a = Array2::<f32>::zeros((128, 128));
//! let image_b = Array2::<f32>::zeros((128, 128));
//!
//! let coeffs_a = curvelet_forward(&image_a, 4).unwrap();
//! let mut fused = curvelet_forward(&image_b, 4).unwrap();
//!
//! // For each detail subband, keep the coefficient with larger magnitude
//! for (s, scale_a) in coeffs_a.detail.iter().enumerate() {
//!     for (d, dir_a) in scale_a.iter().enumerate() {
//!         for (target, source) in fused.detail[s][d].iter_mut().zip(dir_a.iter()) {
//!             if source.norm() > target.norm() {
//!                 *target = *source;
//!             }
//!         }
//!     }
//! }
//!
//! let fused_image = curvelet_inverse(&fused).unwrap();
//! ```
//!
//! ## RGB / multi-channel images
//!
//! The API operates on single-channel `Array2<f32>`. For multi-channel
//! images, transform each channel independently:
//!
//! ```rust
//! # use ndarray::Array2;
//! # use nauticuvs::{curvelet_forward, curvelet_inverse};
//! # let red = Array2::<f32>::zeros((64, 64));
//! # let green = Array2::<f32>::zeros((64, 64));
//! # let blue = Array2::<f32>::zeros((64, 64));
//! let mut coeffs_r = curvelet_forward(&red, 4).unwrap();
//! let mut coeffs_g = curvelet_forward(&green, 4).unwrap();
//! let mut coeffs_b = curvelet_forward(&blue, 4).unwrap();
//!
//! // Process each channel (e.g., denoise)
//! coeffs_r.hard_threshold(0.05);
//! coeffs_g.hard_threshold(0.05);
//! coeffs_b.hard_threshold(0.05);
//!
//! let r = curvelet_inverse(&coeffs_r).unwrap();
//! let g = curvelet_inverse(&coeffs_g).unwrap();
//! let b = curvelet_inverse(&coeffs_b).unwrap();
//! ```
//!
//! ## Coefficient structure
//!
//! The [`CurveletCoeffs`] struct stores three tiers:
//!
//! ```text
//! ┌────────────────────────────────────────────┐
//! │ coarse   (1 isotropic subband, low-freq)   │
//! ├────────────────────────────────────────────┤
//! │ detail[0]  (N₁ directional subbands)       │
//! │ detail[1]  (N₂ directional subbands)       │
//! │   ...                                      │
//! ├────────────────────────────────────────────┤
//! │ fine     (1 isotropic subband, high-freq)  │
//! └────────────────────────────────────────────┘
//! ```
//!
//! Each subband is an `Array2<Complex<f64>>` of the same size as the
//! (padded) input. Thresholding operates only on detail coefficients,
//! leaving coarse and fine subbands untouched to preserve low-frequency
//! content and fine texture.
//!
//! ## Parallelism
//!
//! Enable the `parallel` feature to process directional subbands
//! concurrently via [rayon](https://docs.rs/rayon):
//!
//! ```toml
//! [dependencies]
//! nauticuvs = { version = "0.1", features = ["parallel"] }
//! ```
//!
//! ## Precision
//!
//! - **Public API:** accepts `Array2<f32>` images, returns `Array2<f32>`.
//! - **Internal computation:** all FFTs, windows, and coefficients use `f64`.
//! - **Reconstruction accuracy:** relative L2 error < 10⁻⁶ for unmodified
//!   coefficients, verified from 16×16 to 64×64 with 2–5 scales.
//!
//! ## Error handling
//!
//! All fallible operations return `Result<T, CurveletError>`:
//!
//! - [`CurveletError::ZeroDimension`] — image has 0 rows or columns
//! - [`CurveletError::NonFiniteInput`] — image contains NaN or Inf
//! - [`CurveletError::InvalidScaleCount`] — `num_scales` not in [2, 10]
//! - [`CurveletError::InvalidDirectionCount`] — directions not ≥ 4 and multiple of 4
//! - [`CurveletError::DirectionCountMismatch`] — wrong number of entries in per-scale directions
//! - [`CurveletError::InconsistentCoeffs`] — coefficient structure doesn't match config
//!
//! ## References
//!
//! - Candès, Demanet, Donoho, Ying (2006). "Fast Discrete Curvelet Transforms."
//!   *Multiscale Modeling & Simulation*, 5(3), 861–899.
//! - Candès & Donoho (2004). "New tight frames of curvelets and optimal
//!   representations of objects with piecewise C² singularities."

mod coeffs;
mod config;
mod error;
mod fft;
mod forward;
mod inverse;
mod utils;
mod windows;
mod wrapping;

pub use coeffs::CurveletCoeffs;
pub use config::CurveletConfig;
pub use error::CurveletError;

use ndarray::Array2;

/// Compute the forward curvelet transform with default configuration.
///
/// Uses Meyer windows and the standard direction-doubling scheme:
/// 16 directions at the coarsest detail scale, doubling every two scales.
///
/// # Arguments
///
/// * `image` — 2D `f32` grayscale image (arbitrary dimensions; padded internally).
/// * `num_scales` — Number of decomposition scales (including coarsest and finest). ≥ 2, ≤ 10.
///
/// # Errors
///
/// Returns [`CurveletError`] if inputs are invalid (zero dimensions, non-finite
/// values, out-of-range scale count).
pub fn curvelet_forward(
    image: &Array2<f32>,
    num_scales: usize,
) -> Result<CurveletCoeffs, CurveletError> {
    let config = CurveletConfig::new(num_scales)?;
    curvelet_forward_config(image, &config)
}

/// Compute the forward curvelet transform with custom configuration.
///
/// Allows control over the number of directions at the finest detail scale
/// and per-scale direction overrides.
pub fn curvelet_forward_config(
    image: &Array2<f32>,
    config: &CurveletConfig,
) -> Result<CurveletCoeffs, CurveletError> {
    forward::forward_transform(image, config)
}

/// Compute the inverse curvelet transform (reconstruction).
///
/// Given a [`CurveletCoeffs`] structure (possibly modified for denoising
/// or fusion), reconstruct the 2D image.
///
/// Reconstruction fidelity: relative L2 error < 10⁻⁶ for unmodified coefficients.
pub fn curvelet_inverse(coeffs: &CurveletCoeffs) -> Result<Array2<f32>, CurveletError> {
    inverse::inverse_transform(coeffs)
}

#[cfg(test)]
mod tests;
