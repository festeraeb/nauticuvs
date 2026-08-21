//! Unit and integration tests for the curvelet transform crate.

use ndarray::Array2;

use crate::{curvelet_forward, curvelet_inverse, CurveletConfig, CurveletError};

/// Helper: compute relative L2 error between two f32 arrays.
fn relative_l2_error(original: &Array2<f32>, reconstructed: &Array2<f32>) -> f64 {
    let diff_norm_sq: f64 = original
        .iter()
        .zip(reconstructed.iter())
        .map(|(a, b)| (*a as f64 - *b as f64).powi(2))
        .sum();
    let orig_norm_sq: f64 = original.iter().map(|a| (*a as f64).powi(2)).sum();
    if orig_norm_sq < 1e-24 {
        return 0.0;
    }
    (diff_norm_sq / orig_norm_sq).sqrt()
}

// ===== Configuration tests =====

#[test]
fn test_config_valid() {
    let cfg = CurveletConfig::new(5).unwrap();
    assert_eq!(cfg.num_scales, 5);
    assert_eq!(cfg.num_detail_scales(), 3);
}

#[test]
fn test_config_invalid_scale_count() {
    assert!(matches!(
        CurveletConfig::new(1),
        Err(CurveletError::InvalidScaleCount(1))
    ));
    assert!(matches!(
        CurveletConfig::new(11),
        Err(CurveletError::InvalidScaleCount(11))
    ));
}

#[test]
fn test_config_direction_doubling() {
    let cfg = CurveletConfig::new(5).unwrap();
    let d0 = cfg.directions_at_detail_scale(0);
    let d1 = cfg.directions_at_detail_scale(1);
    let d2 = cfg.directions_at_detail_scale(2);
    assert!(d2 >= d1);
    assert!(d1 >= d0);
    assert_eq!(d2, 32);
}

#[test]
fn test_config_custom_directions() {
    let cfg = CurveletConfig::new(5)
        .unwrap()
        .with_directions_per_scale(vec![8, 16, 32])
        .unwrap();
    assert_eq!(cfg.directions_at_detail_scale(0), 8);
    assert_eq!(cfg.directions_at_detail_scale(1), 16);
    assert_eq!(cfg.directions_at_detail_scale(2), 32);
}

#[test]
fn test_config_custom_directions_wrong_length() {
    let result = CurveletConfig::new(5)
        .unwrap()
        .with_directions_per_scale(vec![8, 16]);
    assert!(matches!(
        result,
        Err(CurveletError::DirectionCountMismatch {
            expected: 3,
            got: 2
        })
    ));
}

#[test]
fn test_config_invalid_direction_count() {
    let result = CurveletConfig::new(5).unwrap().with_finest_directions(3);
    assert!(matches!(
        result,
        Err(CurveletError::InvalidDirectionCount(3))
    ));
}

// ===== Input validation tests =====

#[test]
fn test_zero_dimension_error() {
    let img = Array2::<f32>::zeros((0, 10));
    assert!(matches!(
        curvelet_forward(&img, 3),
        Err(CurveletError::ZeroDimension { .. })
    ));
}

#[test]
fn test_non_finite_error() {
    let mut img = Array2::<f32>::zeros((8, 8));
    img[[0, 0]] = f32::NAN;
    assert!(matches!(
        curvelet_forward(&img, 3),
        Err(CurveletError::NonFiniteInput)
    ));
}

// ===== Reconstruction tests (critical correctness) =====

#[test]
fn test_reconstruction_zeros() {
    let img = Array2::<f32>::zeros((16, 16));
    let coeffs = curvelet_forward(&img, 3).unwrap();
    let recon = curvelet_inverse(&coeffs).unwrap();
    assert_eq!(recon.dim(), img.dim());
    let max_err: f32 = recon.iter().map(|v| v.abs()).fold(0.0f32, f32::max);
    assert!(
        max_err < 1e-10,
        "zero image reconstruction error: {max_err}"
    );
}

#[test]
fn test_reconstruction_constant() {
    let img = Array2::from_elem((16, 16), 42.0f32);
    let coeffs = curvelet_forward(&img, 3).unwrap();
    let recon = curvelet_inverse(&coeffs).unwrap();
    let err = relative_l2_error(&img, &recon);
    assert!(err < 1e-6, "constant image reconstruction error: {err}");
}

#[test]
fn test_reconstruction_gradient() {
    let n = 32;
    let img = Array2::from_shape_fn((n, n), |(r, c)| (r as f32 + c as f32) / (2.0 * n as f32));
    let coeffs = curvelet_forward(&img, 4).unwrap();
    let recon = curvelet_inverse(&coeffs).unwrap();
    let err = relative_l2_error(&img, &recon);
    assert!(err < 1e-6, "gradient reconstruction error: {err}");
}

#[test]
fn test_reconstruction_sinusoid() {
    let n = 64;
    let img = Array2::from_shape_fn((n, n), |(r, c)| {
        let x = r as f32 / n as f32;
        let y = c as f32 / n as f32;
        (2.0 * std::f32::consts::PI * 3.0 * x).sin() + (2.0 * std::f32::consts::PI * 5.0 * y).cos()
    });
    let coeffs = curvelet_forward(&img, 5).unwrap();
    let recon = curvelet_inverse(&coeffs).unwrap();
    let err = relative_l2_error(&img, &recon);
    assert!(err < 1e-6, "sinusoid reconstruction error: {err}");
}

#[test]
fn test_reconstruction_random() {
    use rand::Rng;
    let n = 32;
    let mut rng = rand::thread_rng();
    let img = Array2::from_shape_fn((n, n), |_| rng.gen::<f32>());
    let coeffs = curvelet_forward(&img, 4).unwrap();
    let recon = curvelet_inverse(&coeffs).unwrap();
    let err = relative_l2_error(&img, &recon);
    assert!(err < 1e-6, "random reconstruction error: {err}");
}

#[test]
fn test_reconstruction_non_square() {
    let img = Array2::from_shape_fn((24, 32), |(r, c)| (r + c) as f32);
    let coeffs = curvelet_forward(&img, 3).unwrap();
    let recon = curvelet_inverse(&coeffs).unwrap();
    assert_eq!(recon.dim(), (24, 32));
    let err = relative_l2_error(&img, &recon);
    assert!(err < 1e-6, "non-square reconstruction error: {err}");
}

#[test]
fn test_reconstruction_5_scales_64() {
    // Previously the worst case: 64×64 with 5 scales
    let n = 64;
    let img = Array2::from_shape_fn((n, n), |(r, c)| {
        ((r as f32 * 0.1).sin() + (c as f32 * 0.07).cos()) * 100.0
    });
    let coeffs = curvelet_forward(&img, 5).unwrap();
    let recon = curvelet_inverse(&coeffs).unwrap();
    let err = relative_l2_error(&img, &recon);
    assert!(err < 1e-6, "5-scale 64×64 reconstruction error: {err}");
}

// ===== Coefficient structure =====

#[test]
fn test_coefficient_structure() {
    let img = Array2::from_shape_fn((32, 32), |(r, c)| (r * c) as f32 / 1024.0);
    let coeffs = curvelet_forward(&img, 4).unwrap();
    assert_eq!(coeffs.detail.len(), 2);
    let cfg = CurveletConfig::new(4).unwrap();
    for d in 0..2 {
        assert_eq!(coeffs.detail[d].len(), cfg.directions_at_detail_scale(d));
    }
}

// ===== Thresholding =====

#[test]
fn test_hard_threshold() {
    let img = Array2::from_shape_fn((16, 16), |(r, c)| (r + c) as f32);
    let mut coeffs = curvelet_forward(&img, 3).unwrap();
    let before = coeffs.num_coeffs();
    coeffs.hard_threshold(100.0);
    let zero_count: usize = coeffs
        .detail
        .iter()
        .flat_map(|s| s.iter())
        .flat_map(|sb| sb.iter())
        .filter(|c| c.norm() < 1e-10)
        .count();
    assert!(zero_count > 0);
    assert_eq!(coeffs.num_coeffs(), before);
}

#[test]
fn test_soft_threshold() {
    let img = Array2::from_shape_fn((16, 16), |(r, c)| (r + c) as f32);
    let mut coeffs = curvelet_forward(&img, 3).unwrap();
    coeffs.soft_threshold(50.0);
    for scale in &coeffs.detail {
        for sb in scale {
            for c in sb.iter() {
                assert!(c.norm() >= 0.0);
            }
        }
    }
}

// ===== Edge cases =====

#[test]
fn test_two_scales() {
    let img = Array2::from_elem((8, 8), 1.0f32);
    let coeffs = curvelet_forward(&img, 2).unwrap();
    assert!(coeffs.detail.is_empty());
    let recon = curvelet_inverse(&coeffs).unwrap();
    let err = relative_l2_error(&img, &recon);
    assert!(err < 1e-6, "2-scale reconstruction error: {err}");
}
