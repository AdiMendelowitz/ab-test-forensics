import abforensics


def test_all_names_are_attributes():
    for name in abforensics.__all__:
        assert hasattr(abforensics, name)


def test_all_names_are_sorted():
    assert abforensics.__all__ == sorted(abforensics.__all__)


def test_ratio_of_sums_export():
    from abforensics import ratio_of_sums
    from abforensics.metrics.ratio import ratio_of_sums as defined

    assert ratio_of_sums is defined


def test_naive_event_rate_export():
    from abforensics import naive_event_rate
    from abforensics.metrics.ratio import naive_event_rate as defined

    assert naive_event_rate is defined


def test_jackknife_variance_export():
    from abforensics import jackknife_variance
    from abforensics.inference.resampling import jackknife_variance as defined

    assert jackknife_variance is defined


def test_cluster_bootstrap_export():
    from abforensics import cluster_bootstrap
    from abforensics.inference.resampling import cluster_bootstrap as defined

    assert cluster_bootstrap is defined


def test_difference_in_means_export():
    from abforensics import difference_in_means
    from abforensics.inference.means import difference_in_means as defined

    assert difference_in_means is defined


def test_ratio_of_sums_result_export():
    from abforensics import RatioOfSumsResult
    from abforensics.results import RatioOfSumsResult as Defined

    assert RatioOfSumsResult is Defined


def test_jackknife_result_export():
    from abforensics import JackknifeResult
    from abforensics.results import JackknifeResult as Defined

    assert JackknifeResult is Defined


def test_bootstrap_result_export():
    from abforensics import BootstrapResult
    from abforensics.results import BootstrapResult as Defined

    assert BootstrapResult is Defined


def test_mean_difference_result_export():
    from abforensics import MeanDifferenceResult
    from abforensics.results import MeanDifferenceResult as Defined

    assert MeanDifferenceResult is Defined
