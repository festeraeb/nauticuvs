//! Error types for the curvelet transform crate.

use thiserror::Error;

/// Errors produced by curvelet transform operations.
#[derive(Debug, Error)]
pub enum CurveletError {
    /// Image has zero rows or columns.
    #[error("image dimensions must be non-zero, got {rows}×{cols}")]
    ZeroDimension { rows: usize, cols: usize },

    /// Image contains NaN or infinity values.
    #[error("image contains non-finite values (NaN or Inf)")]
    NonFiniteInput,

    /// Scale count is outside the allowed range [2, 10].
    #[error("num_scales must be in [2, 10], got {0}")]
    InvalidScaleCount(usize),

    /// Custom directions vector has the wrong length.
    #[error(
        "directions_per_scale has length {got}, expected {expected} \
         (num_scales - 2 detail scales)"
    )]
    DirectionCountMismatch { expected: usize, got: usize },

    /// A direction count must be positive and a multiple of 4.
    #[error("direction count must be ≥ 4 and a multiple of 4, got {0}")]
    InvalidDirectionCount(usize),

    /// Coefficient structure is internally inconsistent.
    #[error("coefficient structure is inconsistent: {0}")]
    InconsistentCoeffs(String),
}
