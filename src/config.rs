//! Transform configuration.
//!
//! The [`CurveletConfig`] struct controls the decomposition parameters:
//! number of scales, directions per scale, and transform variant.

use crate::error::CurveletError;

/// Configuration for the curvelet transform.
///
/// # Direction scheme
///
/// The standard CurveLab convention uses direction doubling every two scales.
/// For `num_scales = 5` the decomposition is:
///
/// | Scale index | Role   | Directions |
/// |-------------|--------|------------|
/// | 0           | coarse | 1          |
/// | 1           | detail | 16         |
/// | 2           | detail | 16         |
/// | 3           | detail | 32         |
/// | 4           | fine   | 1          |
///
/// The "detail" scales (indices `1 .. num_scales-1`) carry directional
/// subbands. Scales 0 (coarsest) and `num_scales-1` (finest) are
/// isotropic (single subband each).
#[derive(Debug, Clone)]
pub struct CurveletConfig {
    /// Total number of scales (including coarse + fine). Must be in [2, 10].
    pub num_scales: usize,

    /// Number of directions at the **finest detail scale** (scale index
    /// `num_scales - 2`). Must be ≥ 4 and a multiple of 4.
    /// Coarser detail scales halve directions every two scales.
    /// Default: 32.
    pub finest_scale_directions: usize,

    /// Optional explicit direction counts for each detail scale
    /// (indices 1 through `num_scales - 2`). If `Some`, overrides
    /// `finest_scale_directions`. Each entry must be ≥ 4 and a multiple of 4.
    pub directions_per_scale: Option<Vec<usize>>,

    /// Original (unpadded) image dimensions, filled in during forward transform.
    /// Users should not set this.
    pub(crate) original_rows: usize,
    /// Original (unpadded) image columns.
    pub(crate) original_cols: usize,
    /// Padded image dimension (power of 2).
    pub(crate) padded_size: usize,
}

impl CurveletConfig {
    /// Create a config with default direction scheme.
    ///
    /// `num_scales` must be in `[2, 10]`.
    pub fn new(num_scales: usize) -> Result<Self, CurveletError> {
        if !(2..=10).contains(&num_scales) {
            return Err(CurveletError::InvalidScaleCount(num_scales));
        }
        Ok(Self {
            num_scales,
            finest_scale_directions: 32,
            directions_per_scale: None,
            original_rows: 0,
            original_cols: 0,
            padded_size: 0,
        })
    }

    /// Builder: set the finest-scale direction count.
    pub fn with_finest_directions(mut self, d: usize) -> Result<Self, CurveletError> {
        if d < 4 || !d.is_multiple_of(4) {
            return Err(CurveletError::InvalidDirectionCount(d));
        }
        self.finest_scale_directions = d;
        Ok(self)
    }

    /// Builder: provide explicit direction counts per detail scale.
    pub fn with_directions_per_scale(mut self, dirs: Vec<usize>) -> Result<Self, CurveletError> {
        let num_detail = self.num_detail_scales();
        if dirs.len() != num_detail {
            return Err(CurveletError::DirectionCountMismatch {
                expected: num_detail,
                got: dirs.len(),
            });
        }
        for &d in &dirs {
            if d < 4 || !d.is_multiple_of(4) {
                return Err(CurveletError::InvalidDirectionCount(d));
            }
        }
        self.directions_per_scale = Some(dirs);
        Ok(self)
    }

    /// Number of detail scales (those with directional subbands).
    /// This is `num_scales - 2` (excluding coarsest and finest).
    #[inline]
    pub fn num_detail_scales(&self) -> usize {
        self.num_scales.saturating_sub(2)
    }

    /// Get the number of angular directions at detail scale `detail_idx`
    /// (0-based among detail scales, where 0 = coarsest detail).
    ///
    /// Uses explicit `directions_per_scale` if set, otherwise computes
    /// via the standard doubling scheme.
    pub fn directions_at_detail_scale(&self, detail_idx: usize) -> usize {
        if let Some(ref dirs) = self.directions_per_scale {
            return dirs[detail_idx];
        }
        // Standard doubling scheme: finest detail scale has `finest_scale_directions`,
        // and we halve every two scales going coarser (minimum 8).
        let n_detail = self.num_detail_scales();
        if n_detail == 0 {
            return 0;
        }
        let finest_idx = n_detail - 1;
        let levels_from_finest = finest_idx - detail_idx;
        let halvings = levels_from_finest / 2;
        let dirs = self.finest_scale_directions >> halvings;
        dirs.max(8)
    }
}
