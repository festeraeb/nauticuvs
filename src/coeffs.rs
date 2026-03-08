//! Curvelet coefficient storage.
//!
//! The [`CurveletCoeffs`] structure stores the output of a forward curvelet
//! transform as a hierarchy of 2D arrays using `f64` precision internally.

use ndarray::Array2;
use num_complex::{Complex, ComplexFloat};

use crate::config::CurveletConfig;
use crate::error::CurveletError;

/// Multi-scale, multi-directional curvelet coefficients (f64 precision).
///
/// Produced by [`crate::curvelet_forward`] and consumed by
/// [`crate::curvelet_inverse`].
///
/// # Structure
///
/// ```text
/// Scale 0 (coarse):  1 isotropic subband       → coarse
/// Scale 1 (detail):  N₁ directional subbands    → detail[0]
///   ...
/// Scale J-1 (fine):  1 isotropic subband        → fine
/// ```
#[derive(Debug, Clone)]
pub struct CurveletCoeffs {
    /// Coarsest scale (low-frequency) coefficients.
    pub coarse: Array2<Complex<f64>>,

    /// Detail-scale coefficients. `detail[s][d]` is the subband for
    /// detail scale `s`, direction `d`.
    pub detail: Vec<Vec<Array2<Complex<f64>>>>,

    /// Finest scale (high-frequency) coefficients.
    pub fine: Array2<Complex<f64>>,

    /// Configuration snapshot. Needed by the inverse transform.
    pub(crate) config: CurveletConfig,
}

impl CurveletCoeffs {
    /// Total number of complex coefficients across all subbands.
    pub fn num_coeffs(&self) -> usize {
        let coarse_n = self.coarse.len();
        let fine_n = self.fine.len();
        let detail_n: usize = self
            .detail
            .iter()
            .flat_map(|scale| scale.iter())
            .map(|sb| sb.len())
            .sum();
        coarse_n + detail_n + fine_n
    }

    /// Validate internal consistency.
    pub(crate) fn validate(&self) -> Result<(), CurveletError> {
        let n_detail = self.config.num_detail_scales();
        if self.detail.len() != n_detail {
            return Err(CurveletError::InconsistentCoeffs(format!(
                "expected {} detail scales, got {}",
                n_detail,
                self.detail.len()
            )));
        }
        for (i, scale) in self.detail.iter().enumerate() {
            let expected_dirs = self.config.directions_at_detail_scale(i);
            if scale.len() != expected_dirs {
                return Err(CurveletError::InconsistentCoeffs(format!(
                    "detail scale {} expected {} directions, got {}",
                    i,
                    expected_dirs,
                    scale.len()
                )));
            }
        }
        Ok(())
    }

    /// Hard thresholding on detail coefficients.
    ///
    /// Coefficients with magnitude below `threshold` are set to zero.
    /// Coarse and fine scales are untouched.
    pub fn hard_threshold(&mut self, threshold: f64) {
        for scale in &mut self.detail {
            for subband in scale {
                for c in subband.iter_mut() {
                    if c.abs() < threshold {
                        *c = Complex::ZERO;
                    }
                }
            }
        }
    }

    /// Soft thresholding on detail coefficients.
    ///
    /// `c → c · max(0, 1 - threshold/|c|)`.
    pub fn soft_threshold(&mut self, threshold: f64) {
        for scale in &mut self.detail {
            for subband in scale {
                for c in subband.iter_mut() {
                    let mag = c.abs();
                    if mag < threshold {
                        *c = Complex::ZERO;
                    } else {
                        *c *= (mag - threshold) / mag;
                    }
                }
            }
        }
    }
}
